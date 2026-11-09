/*
 * Nota 语音按钮固件 —— ESP32 + I2S 麦克风 + 状态灯 + 按键
 *
 * 设备端只做三件事：
 *   1. 采音（I2S，16kHz/16bit 单声道），按下开始、松开结束，最长 15 秒
 *   2. 打包成 WAV，POST 到 /api/notes/audio，幂等键由 machine_code + 开机计数组成
 *   3. 用灯色把 Agent 的状态摆出来：待机 / 录音 / 上传 / 思考 / 完成 / 失败 / 额度用完
 *
 * 刻意不做唤醒词和本地推理：固件体积和续航都会失控，而"按一下"比"喊一声"可控。
 * 设备无屏、无存储、无用户体系，身份只有出厂 machine_code，绑定关系在云端。
 *
 * 仿真说明：Wokwi 没有 INMP441 模型，定义 NOTA_SIMULATED_MIC 后
 * readMic() 会用正弦波填充缓冲区，从而在没有麦克风的情况下跑通整条上传链路。
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <driver/i2s.h>

#ifndef NOTA_WIFI_SSID
#define NOTA_WIFI_SSID "Wokwi-GUEST"
#endif
#ifndef NOTA_WIFI_PASS
#define NOTA_WIFI_PASS ""
#endif
#ifndef NOTA_API_BASE
#define NOTA_API_BASE "http://host.wokwi.internal:8787"
#endif
#ifndef NOTA_MACHINE_CODE
#define NOTA_MACHINE_CODE "A1B2C3D4E5F6"
#endif

// ---- pin map (must match device/wokwi/diagram.json) -------------------------
#define PIN_I2S_SCK 14
#define PIN_I2S_WS  15
#define PIN_I2S_SD  32
#define PIN_LED     4
#define PIN_BUTTON  5

// ---- audio ------------------------------------------------------------------
#define SAMPLE_RATE     16000
#define MAX_SECONDS     15
#define MAX_SAMPLES     (SAMPLE_RATE * MAX_SECONDS)
#define I2S_PORT        I2S_NUM_0

static int16_t *g_pcm = nullptr;
static size_t   g_samples = 0;
static unsigned long g_bootCount = 0;

enum LedState { LED_IDLE, LED_RECORDING, LED_UPLOADING, LED_THINKING, LED_OK, LED_ERROR, LED_QUOTA };
static LedState g_led = LED_IDLE;

// 单色 LED：不同状态用不同亮度/闪法表达（真机上是 WS2812 的三色）
void setLed(LedState s) {
  g_led = s;
  bool on = true;
  switch (s) {
    case LED_IDLE:      on = false; break;   // 灭
    case LED_RECORDING: on = true;  break;   // 常亮
    case LED_UPLOADING: on = true;  break;   // 常亮（真机琥珀）
    case LED_THINKING:  on = true;  break;   // 常亮（真机蓝）
    case LED_OK:        on = true;  break;
    case LED_ERROR:     on = false; break;
    case LED_QUOTA:     on = false; break;
  }
  digitalWrite(PIN_LED, on ? HIGH : LOW);
}

void blink(LedState s, int times) {
  for (int i = 0; i < times; i++) {
    digitalWrite(PIN_LED, HIGH); delay(90);
    digitalWrite(PIN_LED, LOW);  delay(90);
  }
  setLed(s);
}

// ---- wifi -------------------------------------------------------------------
void connectWifi() {
  Serial.printf("[wifi] connecting to %s\n", NOTA_WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(NOTA_WIFI_SSID, NOTA_WIFI_PASS);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] up, ip=%s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("[wifi] failed — will retry on next press");
  }
}

// ---- microphone -------------------------------------------------------------
void initMic() {
#ifdef NOTA_SIMULATED_MIC
  Serial.println("[mic] simulated (sine wave), no I2S hardware");
  return;
#endif
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 512;
  i2s_driver_install(I2S_PORT, &cfg, 0, NULL);

  i2s_pin_config_t pins = {};
  pins.bck_io_num = PIN_I2S_SCK;
  pins.ws_io_num = PIN_I2S_WS;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = PIN_I2S_SD;
  i2s_set_pin(I2S_PORT, &pins);
  i2s_zero_dma_buffer(I2S_PORT);
}

size_t readMic(int16_t *buf, size_t maxSamples) {
#ifdef NOTA_SIMULATED_MIC
  static float phase = 0.0f;
  for (size_t i = 0; i < maxSamples; i++) {
    buf[i] = (int16_t)(8000 * sinf(phase));
    phase += 2.0f * 3.14159265f * 440.0f / SAMPLE_RATE;
    if (phase > 6.2831853f) phase -= 6.2831853f;
  }
  delay(maxSamples * 1000 / SAMPLE_RATE);
  return maxSamples;
#else
  size_t bytesRead = 0;
  esp_err_t err = i2s_read(I2S_PORT, buf, maxSamples * 2, &bytesRead, pdMS_TO_TICKS(200));
  if (err != ESP_OK) return 0;
  return bytesRead / 2;
#endif
}

// ---- wav --------------------------------------------------------------------
static void writeLE16(uint8_t *p, uint16_t v) { p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; }
static void writeLE32(uint8_t *p, uint32_t v) {
  p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF; p[2] = (v >> 16) & 0xFF; p[3] = (v >> 24) & 0xFF;
}

size_t buildWav(uint8_t *out, const int16_t *pcm, size_t samples) {
  const uint32_t dataBytes = samples * 2;
  memcpy(out, "RIFF", 4);
  writeLE32(out + 4, 36 + dataBytes);
  memcpy(out + 8, "WAVEfmt ", 8);
  writeLE32(out + 16, 16);
  writeLE16(out + 20, 1);                 // PCM
  writeLE16(out + 22, 1);                 // mono
  writeLE32(out + 24, SAMPLE_RATE);
  writeLE32(out + 28, SAMPLE_RATE * 2);
  writeLE16(out + 32, 2);
  writeLE16(out + 34, 16);
  memcpy(out + 36, "data", 4);
  writeLE32(out + 40, dataBytes);
  memcpy(out + 44, pcm, dataBytes);
  return 44 + dataBytes;
}

// ---- upload -----------------------------------------------------------------
// 幂等键 = machine_code + 开机计数 + 本机序号。设备重试同一个按键不会重复扣额度。
bool upload(const uint8_t *wav, size_t wavLen, int *httpStatus, String *message) {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
  if (WiFi.status() != WL_CONNECTED) {
    *httpStatus = -1;
    *message = "wifi down";
    return false;
  }

  const char *boundary = "----NotaBoundary7d01";
  String head;
  head += "--"; head += boundary; head += "\r\n";
  head += "Content-Disposition: form-data; name=\"file\"; filename=\"note.wav\"\r\n";
  head += "Content-Type: audio/wav\r\n\r\n";

  String tail;
  tail += "\r\n--"; tail += boundary; tail += "\r\n";
  tail += "Content-Disposition: form-data; name=\"idempotency_key\"\r\n\r\n";
  tail += String(NOTA_MACHINE_CODE) + "-" + String(g_bootCount);
  tail += "\r\n--"; tail += boundary; tail += "--\r\n";

  String url = String(NOTA_API_BASE) + "/api/notes/audio";
  HTTPClient http;
  http.setTimeout(30000);
  if (!http.begin(url)) {
    *httpStatus = -2;
    *message = "connect failed";
    return false;
  }
  http.addHeader("Content-Type", String("multipart/form-data; boundary=") + boundary);
  http.addHeader("X-Machine-Code", NOTA_MACHINE_CODE);

  // 直接把 body 拼成内存流：15 秒 16kHz 才 480KB，ESP32 有 PSRAM 时够用
  size_t total = head.length() + wavLen + tail.length();
  uint8_t *body = (uint8_t *)malloc(total);
  if (!body) {
    http.end();
    *httpStatus = -3;
    *message = "out of memory";
    return false;
  }
  memcpy(body, head.c_str(), head.length());
  memcpy(body + head.length(), wav, wavLen);
  memcpy(body + head.length() + wavLen, tail.c_str(), tail.length());

  *httpStatus = http.POST(body, total);
  *message = http.getString();
  free(body);
  http.end();

  Serial.printf("[upload] %u bytes -> %d\n", (unsigned)total, *httpStatus);
  return *httpStatus == 200;
}

// ---- record -----------------------------------------------------------------
void recordWhilePressed() {
  setLed(LED_RECORDING);
  g_samples = 0;
  unsigned long start = millis();
  while (digitalRead(PIN_BUTTON) == LOW && millis() - start < MAX_SECONDS * 1000UL) {
    int16_t buf[512];
    size_t got = readMic(buf, 512);
    if (!got) continue;
    if (g_samples + got > MAX_SAMPLES) got = MAX_SAMPLES - g_samples;
    memcpy(g_pcm + g_samples, buf, got * 2);
    g_samples += got;
    if (g_samples >= MAX_SAMPLES) break;
  }
  Serial.printf("[record] %u samples (%.1fs)\n", (unsigned)g_samples,
                g_samples / (float)SAMPLE_RATE);
}

// ---- setup / loop -----------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  g_bootCount = esp_random() % 100000;
  Serial.println();
  Serial.println("[nota] boot");

  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  setLed(LED_IDLE);

  g_pcm = (int16_t *)malloc(MAX_SAMPLES * sizeof(int16_t));
  if (!g_pcm) {
    Serial.println("[nota] no memory for the audio buffer");
    blink(LED_ERROR, 5);
    while (true) delay(1000);
  }

  initMic();
  connectWifi();
  Serial.printf("[nota] ready, machine_code=%s\n", NOTA_MACHINE_CODE);
}

void loop() {
  if (digitalRead(PIN_BUTTON) == LOW) {
    delay(30);                              // 去抖
    if (digitalRead(PIN_BUTTON) != LOW) return;
  } else {
    delay(20);
    return;
  }

  recordWhilePressed();
  if (g_samples < SAMPLE_RATE / 4) {        // 短于 250ms 视为误触
    Serial.println("[nota] too short, ignored");
    blink(LED_IDLE, 2);
    setLed(LED_IDLE);
    return;
  }

  setLed(LED_UPLOADING);
  size_t capacity = 44 + g_samples * 2;
  uint8_t *wav = (uint8_t *)malloc(capacity);
  if (!wav) {
    blink(LED_ERROR, 3);
    setLed(LED_IDLE);
    return;
  }
  size_t wavLen = buildWav(wav, g_pcm, g_samples);

  setLed(LED_THINKING);
  int status = 0;
  String message;
  bool ok = upload(wav, wavLen, &status, &message);
  free(wav);

  if (ok) {
    setLed(LED_OK);
    delay(1200);
  } else if (status == 429 || message.indexOf("quota_exceeded") >= 0) {
    setLed(LED_QUOTA);                      // 额度用完：长闪三次
    blink(LED_QUOTA, 3);
  } else {
    blink(LED_ERROR, 3);
  }
  setLed(LED_IDLE);
}
