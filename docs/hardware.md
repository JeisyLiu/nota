# 硬件联动

## 一句话

一个 ESP32 语音按钮：按住说话 → 上传 → 灯色告诉你 Agent 现在在干什么。设备没有屏、没有存储、没有用户体系，身份只有出厂烧进去的 `machine_code`。

## 为什么这么设计

Part A 里我把硬件列为第二类客户端，理由是「语音是这个产品的自然入口，而手机 App 需要解锁、点开、找到按钮」。这个理由只有在一个**物理上就绪**的按钮上才成立——所以它必须：

- **无需解锁**：按下就录。
- **状态可见**：不看手机也知道成没成功。
- **无状态**：坏了换一个，绑一下就行。

反过来，设备**不该**做的事也很明确：不做唤醒词（常开麦克风 + 本地模型的功耗和固件体积都不可接受，而且办公室场景喊唤醒词社交成本太高），不做本地推理（这颗芯片不现实），不做存储（语音一旦上传就该由云端负责）。

## 硬件清单

| 件 | 型号 | 说明 |
|---|---|---|
| 主控 | ESP32 DevKit v1（或 ESP32-S3） | 带 WiFi，够跑 TLS |
| 麦克风 | INMP441 | I2S 数字麦，比模拟麦省一路 ADC |
| 灯 | WS2812 / 单色 LED | 状态指示 |
| 按键 | 轻触开关 | 下拉到 GND，内部上拉 |

接线见 `device/wokwi/diagram.json`：

```
GPIO14 → INMP441 SCK
GPIO15 → INMP441 WS
GPIO32 → INMP441 SD
GPIO4  → LED
GPIO5  → 按键（INPUT_PULLUP，按下为 LOW）
```

## 状态机

固件里的灯态和云端 job 状态是一一对应的：

| 灯 | 固件状态 | 触发 | 对应云端 |
|---|---|---|---|
| 灭 | `IDLE` | 待机 | — |
| 常亮 | `RECORDING` | 按键按下 | — |
| 常亮 | `UPLOADING` | 松手，打包 WAV | `POST /api/notes/audio` |
| 常亮 | `THINKING` | 已受理 | `queued → transcribing → agent_running` |
| 亮 1.2s | `OK` | 200 | `done` |
| 闪 3 次 | `ERROR` | 网络/服务端错误 | `failed`（额度已退回） |
| 长闪 3 次 | `QUOTA` | 429 | 免费额度用完 |

## 设备身份与绑定

设备出厂时烧入 `machine_code`（如 `A1B2C3D4E5F6`）。第一次调用接口时服务端自动建设备记录，并把它绑定到 `demo` 用户（MVP 简化）。绑定是**有起止时间的**（`device_bindings.from_at / to_at`），因为设备会被转手：

```sql
CREATE UNIQUE INDEX ux_binding_active
    ON device_bindings(device_id) WHERE to_at IS NULL;
```

同一台设备任何时刻只有一个活跃绑定。转手时关闭旧绑定、开新绑定，历史记录仍归属原用户——这样「我卖掉的设备还能看到我以前的录音」这种问题不会发生。

## 上传协议

固件用 `multipart/form-data` 手工拼 body，字段只有两个：

```
file            note.wav        (16kHz / 16bit / mono PCM)
idempotency_key {machine_code}-{boot_random}-{press_seq}
```

幂等键里带开机随机数，因为 `press_seq` 在重启后会归零——只带序号会让重启后的第一次按键被误判成重放。

音频格式：设备端直接产出 WAV，服务端不做转码（App 端上传的是 m4a，服务端才用 ffmpeg 转）。

## WebSocket 通道

```
GET /ws/device?machine_code=A1B2C3D4E5F6   (upgrade)

← {"type":"hello","machine_code":"...","server":"nota","ts":"..."}
← {"type":"heartbeat","ts":"...","due":[{"id":"rem_x","title":"吃药","fire_at":"..."}]}
→ {"type":"ping"}
← {"type":"pong","ts":"..."}
```

设备保持长连接，服务端每 5 秒推一次心跳，顺带把**已到期的提醒**带给设备——这样即使手机不在身边，按钮自己也能闪灯提醒。断线后设备重连即可，不需要补发（提醒状态在服务端，设备只是显示器）。

## 怎么验证

### 1. 虚拟设备（本机可跑，已验证）

```powershell
python -m backend.main                       # 另开一个窗口
python -m device.sim.virtual_device --text "明天下午三点提醒我给张总发报价单" --stream 10
```

输出（实测）：

```
Nota virtual device  machine_code=A1B2C3D4E5F6  base=http://127.0.0.1:8787
  未配对：本次开机首次说话时会自动绑定到 demo 用户
  [● 录音] 按下说话：明天下午三点提醒我给张总发报价单
  [◍ 上传] 打包 WAV / 提交
  [◎ 思考] job=job_fe176d1d850a
  [◎ 思考] state=transcribing
  [◉ 完成] state=done
  [◉ 完成] 已整理这条语音：我帮你处理了提醒、待办。
      待办：给张总发报价单 2026-11-10T15:00:00+08:00
      提醒：给张总发报价单 @ 2026-11-10T15:00:00+08:00
  quota now 1/10
  [♡] heartbeat ts=2026-11-09T02:48:57+00:00 due=0
```

它走的是**和固件完全相同**的接口，所以它验证的是设备契约，不是替代品。

### 2. Wokwi 仿真（需要 PlatformIO）

```powershell
pip install platformio
cd device/firmware && pio run          # 编译固件
cd ../wokwi && wokwi-cli .             # 或在 VS Code 里 F1 → Wokwi: Start Simulator
```

**限制（诚实说明）**：Wokwi 没有 INMP441 模型，所以仿真时固件用 `NOTA_SIMULATED_MIC` 宏走正弦波填充，能跑通「按键 → 录音 → 打包 WAV → 上传 → 灯态变化」的完整链路，但**没有真实语音内容**，服务端转写出来的不会是那句话。真实的语音链路验证靠上面第 1 种方式（虚拟设备）或真机。

### 3. 真机

烧 `device/firmware/nota_button.ino`，改三处宏：WiFi 账号密码、`NOTA_API_BASE`（指向你机器的局域网 IP）、`NOTA_MACHINE_CODE`（随便一个 12 位十六进制，但全设备唯一）。

## 文件

| 路径 | 内容 |
|---|---|
| `device/firmware/nota_button.ino` | 固件源码（Arduino/PlatformIO） |
| `device/wokwi/diagram.json` | Wokwi 电路图 |
| `device/wokwi/wokwi.toml` | Wokwi 工程配置 |
| `device/wokwi/platformio.ini` | 构建配置（含仿真开关） |
| `device/sim/virtual_device.py` | 虚拟设备，走同一套 HTTP + WS |
