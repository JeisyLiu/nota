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

## 构建

```bash
flutter build apk --debug     # 已验证：build/app/outputs/flutter-apk/app-debug.apk
flutter build web --release   # 已验证：build/web
flutter analyze               # 已验证：0 issue
flutter test                  # 已验证：11 项通过
```

## 结构与取舍

```
lib/
  main.dart              主题 + 入口，NOTA_BASE 注入点
  api/nota_client.dart   唯一与后端协议耦合的地方（不含 dart:io，Web 可复用）
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
