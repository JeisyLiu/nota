#include "audio_recorder.h"

#include <audioclient.h>
#include <mmdeviceapi.h>

#include <chrono>
#include <cstdio>
#include <utility>

namespace {

constexpr int kSampleRate = 16000;
constexpr int kBitsPerSample = 16;
constexpr int kChannels = 1;
constexpr int kBlockAlign = kChannels * kBitsPerSample / 8;
constexpr int kAvgBytesPerSec = kSampleRate * kBlockAlign;

void PutLe32(std::vector<uint8_t>* out, uint32_t value) {
  out->push_back(static_cast<uint8_t>(value & 0xFF));
  out->push_back(static_cast<uint8_t>((value >> 8) & 0xFF));
  out->push_back(static_cast<uint8_t>((value >> 16) & 0xFF));
  out->push_back(static_cast<uint8_t>((value >> 24) & 0xFF));
}

void PutLe16(std::vector<uint8_t>* out, uint16_t value) {
  out->push_back(static_cast<uint8_t>(value & 0xFF));
  out->push_back(static_cast<uint8_t>((value >> 8) & 0xFF));
}

void PutTag(std::vector<uint8_t>* out, const char* tag) {
  for (int i = 0; i < 4; ++i) {
    out->push_back(static_cast<uint8_t>(tag[i]));
  }
}

}  // namespace

AudioRecorder::~AudioRecorder() {
  Cancel();
}

std::string AudioRecorder::HResultMessage(long code) {
  char text[512] = {};
  const DWORD written = ::FormatMessageA(
      FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr,
      static_cast<DWORD>(code), 0, text, sizeof(text) - 1, nullptr);
  std::string message = written > 0 ? std::string(text) : std::string();
  while (!message.empty() && (message.back() == '\n' || message.back() == '\r')) {
    message.pop_back();
  }
  char hex[32] = {};
  ::sprintf_s(hex, "0x%08lX", static_cast<unsigned long>(code));
  if (message.empty()) {
    return hex;
  }
  return message + " (" + hex + ")";
}

bool AudioRecorder::Start(const std::string& path, std::string* error) {
  if (recording_.load()) {
    if (error != nullptr) {
      *error = "already recording";
    }
    return false;
  }

  {
    std::lock_guard<std::mutex> lock(mutex_);
    pcm_.clear();
    read_offset_ = 0;
    pcm_.reserve(static_cast<size_t>(kAvgBytesPerSec) * 30);
  }
  path_ = path;

  std::promise<std::string> ready;
  std::future<std::string> report = ready.get_future();

  running_.store(true);
  try {
    thread_ = std::thread(&AudioRecorder::ThreadMain, this, std::move(ready));
  } catch (...) {
    running_.store(false);
    if (error != nullptr) {
      *error = "cannot start capture thread";
    }
    return false;
  }

  // Wait for the thread to report whether the device actually opened, so Start
  // can fail loudly instead of returning true and recording silence.
  std::string failure = report.get();
  if (!failure.empty()) {
    running_.store(false);
    if (thread_.joinable()) {
      thread_.join();
    }
    if (error != nullptr) {
      *error = failure;
    }
    return false;
  }

  recording_.store(true);
  return true;
}

void AudioRecorder::ThreadMain(std::promise<std::string> ready) {
  const HRESULT com = ::CoInitializeEx(nullptr, COINIT_MULTITHREADED);
  const bool com_ok = SUCCEEDED(com);

  IMMDeviceEnumerator* enumerator = nullptr;
  IMMDevice* device = nullptr;
  IAudioClient* client = nullptr;
  IAudioCaptureClient* capture = nullptr;

  auto fail = [&](const std::string& message) {
    if (capture != nullptr) {
      capture->Release();
    }
    if (client != nullptr) {
      client->Release();
    }
    if (device != nullptr) {
      device->Release();
    }
    if (enumerator != nullptr) {
      enumerator->Release();
    }
    if (com_ok) {
      ::CoUninitialize();
    }
    ready.set_value(message);
  };

  if (!com_ok) {
    ready.set_value("cannot initialise COM: " + HResultMessage(com));
    return;
  }

  HRESULT hr = ::CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
                                  CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                  reinterpret_cast<void**>(&enumerator));
  if (FAILED(hr)) {
    fail("cannot create device enumerator: " + HResultMessage(hr));
    return;
  }

  hr = enumerator->GetDefaultAudioEndpoint(eCapture, eConsole, &device);
  if (FAILED(hr)) {
    fail("没有可用的录音设备（系统未找到麦克风）：" + HResultMessage(hr));
    return;
  }

  hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                        reinterpret_cast<void**>(&client));
  if (FAILED(hr) || client == nullptr) {
    fail("cannot activate audio client: " + HResultMessage(hr));
    return;
  }

  WAVEFORMATEX desired = {};
  desired.wFormatTag = WAVE_FORMAT_PCM;
  desired.nChannels = kChannels;
  desired.nSamplesPerSec = kSampleRate;
  desired.wBitsPerSample = kBitsPerSample;
  desired.nBlockAlign = static_cast<WORD>(kBlockAlign);
  desired.nAvgBytesPerSec = kAvgBytesPerSec;

  // AUTOCONVERTPCM lets the audio engine resample and remix to the format above,
  // so we never negotiate with the device or convert by hand.
  const DWORD flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM |
                      AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY;
  hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED, flags,
                          10000000 /* 1s, 100ns units */, 0, &desired, nullptr);
  if (FAILED(hr)) {
    fail("cannot initialise audio client: " + HResultMessage(hr));
    return;
  }

  hr = client->GetService(__uuidof(IAudioCaptureClient),
                          reinterpret_cast<void**>(&capture));
  if (FAILED(hr) || capture == nullptr) {
    fail("cannot get capture client: " + HResultMessage(hr));
    return;
  }

  hr = client->Start();
  if (FAILED(hr)) {
    fail("cannot start capture: " + HResultMessage(hr));
    return;
  }

  ready.set_value(std::string());

  // Poll rather than use an event handle: the block sizes here are tiny and a
  // 10 ms tick keeps the code linear.
  while (running_.load()) {
    UINT32 packet = 0;
    if (FAILED(capture->GetNextPacketSize(&packet))) {
      break;
    }
    while (packet != 0 && running_.load()) {
      BYTE* data = nullptr;
      UINT32 frames = 0;
      DWORD status = 0;
      if (FAILED(capture->GetBuffer(&data, &frames, &status, nullptr, nullptr))) {
        packet = 0;
        break;
      }
      if (frames > 0) {
        const size_t bytes = static_cast<size_t>(frames) * kBlockAlign;
        std::lock_guard<std::mutex> lock(mutex_);
        if (data != nullptr && (status & AUDCLNT_BUFFERFLAGS_SILENT) == 0) {
          pcm_.insert(pcm_.end(), data, data + bytes);
        } else {
          // Silence still occupies time, so keep the timeline honest.
          pcm_.insert(pcm_.end(), bytes, 0);
        }
      }
      capture->ReleaseBuffer(frames);
      if (FAILED(capture->GetNextPacketSize(&packet))) {
        packet = 0;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }

  client->Stop();
  capture->Release();
  client->Release();
  device->Release();
  enumerator->Release();
  ::CoUninitialize();
}

bool AudioRecorder::Stop(std::string* error) {
  if (!recording_.load()) {
    if (error != nullptr) {
      *error = "not recording";
    }
    return false;
  }

  running_.store(false);
  if (thread_.joinable()) {
    thread_.join();
  }
  recording_.store(false);

  size_t captured = 0;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    captured = pcm_.size();
  }
  if (captured == 0) {
    if (error != nullptr) {
      *error = "no audio captured";
    }
    return false;
  }
  return WriteWav(error);
}

bool AudioRecorder::WriteWav(std::string* error) {
  std::vector<uint8_t> header;
  const uint32_t data_bytes = static_cast<uint32_t>(pcm_.size());
  PutTag(&header, "RIFF");
  PutLe32(&header, 36 + data_bytes);
  PutTag(&header, "WAVE");
  PutTag(&header, "fmt ");
  PutLe32(&header, 16);
  PutLe16(&header, WAVE_FORMAT_PCM);
  PutLe16(&header, static_cast<uint16_t>(kChannels));
  PutLe32(&header, static_cast<uint32_t>(kSampleRate));
  PutLe32(&header, static_cast<uint32_t>(kAvgBytesPerSec));
  PutLe16(&header, static_cast<uint16_t>(kBlockAlign));
  PutLe16(&header, static_cast<uint16_t>(kBitsPerSample));
  PutTag(&header, "data");
  PutLe32(&header, data_bytes);

  FILE* file = nullptr;
  if (::fopen_s(&file, path_.c_str(), "wb") != 0 || file == nullptr) {
    if (error != nullptr) {
      *error = "cannot open output file: " + path_;
    }
    return false;
  }
  bool ok = ::fwrite(header.data(), 1, header.size(), file) == header.size();
  if (ok) {
    std::lock_guard<std::mutex> lock(mutex_);
    ok = ::fwrite(pcm_.data(), 1, pcm_.size(), file) == pcm_.size();
  }
  ::fclose(file);
  if (!ok) {
    if (error != nullptr) {
      *error = "failed writing wav";
    }
    return false;
  }
  return true;
}

void AudioRecorder::Cancel() {
  if (recording_.load()) {
    running_.store(false);
    if (thread_.joinable()) {
      thread_.join();
    }
    recording_.store(false);
  }
  std::lock_guard<std::mutex> lock(mutex_);
  pcm_.clear();
  read_offset_ = 0;
}

std::vector<uint8_t> AudioRecorder::ReadChunk() {
  std::lock_guard<std::mutex> lock(mutex_);
  if (read_offset_ >= pcm_.size()) {
    return {};
  }
  std::vector<uint8_t> out(pcm_.begin() + static_cast<std::ptrdiff_t>(read_offset_),
                           pcm_.end());
  read_offset_ = pcm_.size();
  return out;
}
