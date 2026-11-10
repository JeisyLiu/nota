# nota_app — Nota 移动端

Nota 的 Flutter 客户端。按住说话（或打字）→ 看 Agent 整理出摘要、待办、提醒，并能看到每一步工具调用。

## 先起后端

App 本身不含任何智能，全部逻辑在后端：

```powershell
cd ..\..\          # 回到仓库根目录
.\run.ps1          # http://127.0.0.1:8787
```

## 跑 App

```bash
flutter pub get

# Android 模拟器（10.0.2.2 是宿主机的回环地址）
flutter run --dart-define=NOTA_BASE=http://10.0.2.2:8787

# 真机（换成你电脑的局域网 IP）
flutter run --dart-define=NOTA_BASE=http://192.168.1.23:8787

# 浏览器（不需要模拟器，最快看效果）
flutter run -d chrome --dart-define=NOTA_BASE=http://127.0.0.1:8787
```

后端地址通过编译期常量注入，默认 `http://127.0.0.1:8787`。

## 登录

App 启动先要登录，账号与后台是同一套（种子账号密码统一 `123456`）：

| 用户名 | 说明 |
|---|---|
| `zhangwei` | 普通用户，Free，自带历史记录 |
| `lina` / `chenjing` | 普通用户，Pro |
| `wangfang` / `liuyang` | 普通用户，Free |

也可以在登录页切到「注册」自建账号（用户名 2–32 字符、密码 ≥6 位，固定 Free）。

- **令牌怎么带**：登录成功后后端返回 `token`，App 用 `Authorization: Bearer <token>`。不用 Cookie，因为 `package:http` 不会跨启动保留 Cookie。
- **存在哪**：`shared_preferences`（`nota.token` / `nota.user`），**明文**。演示可接受；生产应换成平台 keystore。
- **过期怎么办**：任何接口返回 401 → `NotaClient.onUnauthorized` → 清凭据 → 回登录页并提示「登录已过期」。
- **本地存储不可用时**：`AppGate` 有 5 秒超时与兜底，读不到就直接进登录页，不会卡在加载圈。

## 构建

```bash
flutter build apk --debug     # 已验证：build/app/outputs/flutter-apk/app-debug.apk
flutter build web --release   # 已验证：build/web
flutter build windows --release  # 已验证：build/windows/x64/runner/Release/nota_app.exe
flutter analyze               # 已验证：0 issue
flutter test                  # 已验证：11 项通过
```

## 录音为什么有两套实现

界面只依赖 `lib/audio/note_recorder.dart` 里的 `NoteRecorder` 接口，平台各自实现：

| 平台 | 实现 | 走什么 |
|---|---|---|
| Android / iOS / Web | `PluginNoteRecorder` | `record` 插件 |
| Windows | `WindowsNoteRecorder` | `windows/runner/audio_recorder.cpp`（WASAPI） |

Windows 单独实现不是洁癖，是因为 `record` 插件的 Windows 后端在这个系统上会崩：
它经过 Media Foundation，打开采集设备时在 `MFPlat.DLL` 里触发访问冲突
（`0xc0000005`），整个进程直接被杀——Dart 的 `try/catch` 拦不住原生崩溃。

排查过程也记录一下，免得后人重走：

1. 先怀疑设备，结果 MME 能打开设备但 `WOM_DONE` 一个回调都不给（采到 0 字节）；
2. `ffmpeg -f dshow` 能正常录到这个麦克风，说明设备本身没问题；
3. 最后换 WASAPI（Windows 自己录音走的就是这层，不是 Media Foundation），成功。

**设备选择始终交给系统**：代码里不传设备 ID，Windows 用
`GetDefaultAudioEndpoint(eCapture)`，插件用平台默认值。插拔麦克风由系统跟随，
程序不做任何枚举或挑选。

WASAPI 的所有调用都在**同一个采集线程**上完成。这不是整洁癖：窗口线程是 STA，
在它上面拿到接口再交给别的线程使用，第一次可能凑巧能用，之后就静默采不到音频——
连续录音坏掉正是这个原因。

## 结构与取舍

```
lib/
  main.dart              主题 + 入口，NOTA_BASE 注入点
  api/nota_client.dart   唯一与后端协议耦合的地方（不含 dart:io，Web 可复用）
  audio/note_recorder.dart NoteRecorder 接口 + 两个平台实现（见上一节）
  models/models.dart     与 JSON 一一对应的数据模型，不做业务判断
  pages/home_page.dart   录音 / 打字 / 最近记录 / 额度
  pages/detail_page.dart 摘要 + 待办 + 提醒 + Agent Trace + 审批卡片
  widgets/               按住说话、额度条、状态徽章
```

- **薄客户端**：所有状态判断（任务状态机、额度、审批）都在后端，App 只负责采集与展示。这符合 Part A 里「智能在云端，端侧只做交互」的结论。
- **轮询而非 SSE**：移动端后台切换时 SSE 连接容易被系统掐断，`waitFor()` 用 1 秒轮询更稳；网页端才用 SSE。
- **不依赖 `dart:io`**：音频以字节上传（`cross_file` 读文件），所以同一份 `NotaClient` 能跑在 Android 和浏览器上。

## 已知限制

- 未在真机或模拟器上跑过（本机没有可用镜像），验证止于 `analyze` 0 issue、11 项测试、APK 与 Web 产物构建成功。
- 没有登录，用户由 `X-User-Id` 头决定，默认 `demo`，对应后端的 MVP 取舍。
- `record` 锁在 5.1.x：5.2 把 `record_platform_interface` 升到 1.6.0，而 `record_linux` 0.7.2 尚未跟进，会导致 kernel_snapshot 编译失败。
