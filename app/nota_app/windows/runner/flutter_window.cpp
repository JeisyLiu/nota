#include "flutter_window.h"

#include <flutter/standard_method_codec.h>

#include <optional>
#include <string>
#include <variant>

#include "flutter/generated_plugin_registrant.h"

FlutterWindow::FlutterWindow(const flutter::DartProject& project)
    : project_(project) {}

FlutterWindow::~FlutterWindow() {}

void FlutterWindow::RegisterAudioChannel() {
  audio_recorder_ = std::make_unique<AudioRecorder>();
  audio_channel_ =
      std::make_unique<flutter::MethodChannel<flutter::EncodableValue>>(
          flutter_controller_->engine()->messenger(), "nota/audio",
          &flutter::StandardMethodCodec::GetInstance());

  audio_channel_->SetMethodCallHandler(
      [this](const flutter::MethodCall<flutter::EncodableValue>& call,
             std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>>
                 result) {
        const std::string& method = call.method_name();

        if (method == "start") {
          std::string path;
          if (const auto* args =
                  std::get_if<flutter::EncodableMap>(call.arguments())) {
            const auto it = args->find(flutter::EncodableValue("path"));
            if (it != args->end()) {
              if (const auto* value =
                      std::get_if<std::string>(&it->second)) {
                path = *value;
              }
            }
          }
          if (path.empty()) {
            result->Error("bad_args", "start needs a path");
            return;
          }
          std::string error;
          if (audio_recorder_->Start(path, &error)) {
            result->Success(flutter::EncodableValue(true));
          } else {
            result->Error("record_failed", error);
          }
          return;
        }

        if (method == "stop") {
          std::string error;
          if (audio_recorder_->Stop(&error)) {
            result->Success(flutter::EncodableValue(true));
          } else {
            result->Error("record_failed", error);
          }
          return;
        }

        if (method == "cancel") {
          audio_recorder_->Cancel();
          result->Success();
          return;
        }

        if (method == "isRecording") {
          result->Success(flutter::EncodableValue(audio_recorder_->recording()));
          return;
        }

        if (method == "readChunk") {
          // PCM captured since the previous call, for the live preview.
          std::vector<uint8_t> chunk = audio_recorder_->ReadChunk();
          result->Success(flutter::EncodableValue(std::move(chunk)));
          return;
        }

        result->NotImplemented();
      });
}

bool FlutterWindow::OnCreate() {
  if (!Win32Window::OnCreate()) {
    return false;
  }

  RECT frame = GetClientArea();

  // The size here must match the window dimensions to avoid unnecessary surface
  // creation / destruction in the startup path.
  flutter_controller_ = std::make_unique<flutter::FlutterViewController>(
      frame.right - frame.left, frame.bottom - frame.top, project_);
  // Ensure that basic setup of the controller was successful.
  if (!flutter_controller_->engine() || !flutter_controller_->view()) {
    return false;
  }
  RegisterPlugins(flutter_controller_->engine());
  RegisterAudioChannel();
  SetChildContent(flutter_controller_->view()->GetNativeWindow());

  flutter_controller_->engine()->SetNextFrameCallback([&]() {
    this->Show();
  });

  // Flutter can complete the first frame before the "show window" callback is
  // registered. The following call ensures a frame is pending to ensure the
  // window is shown. It is a no-op if the first frame hasn't completed yet.
  flutter_controller_->ForceRedraw();

  return true;
}

void FlutterWindow::OnDestroy() {
  // Never leave the microphone open after the window goes away.
  if (audio_recorder_) {
    audio_recorder_->Cancel();
  }
  audio_channel_.reset();
  audio_recorder_.reset();

  if (flutter_controller_) {
    flutter_controller_ = nullptr;
  }

  Win32Window::OnDestroy();
}

LRESULT
FlutterWindow::MessageHandler(HWND hwnd, UINT const message,
                              WPARAM const wparam,
                              LPARAM const lparam) noexcept {
  // Give Flutter, including plugins, an opportunity to handle window messages.
  if (flutter_controller_) {
    std::optional<LRESULT> result =
        flutter_controller_->HandleTopLevelWindowProc(hwnd, message, wparam,
                                                      lparam);
    if (result) {
      return *result;
    }
  }

  switch (message) {
    case WM_FONTCHANGE:
      flutter_controller_->engine()->ReloadSystemFonts();
      break;
  }

  return Win32Window::MessageHandler(hwnd, message, wparam, lparam);
}
