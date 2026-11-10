#ifndef RUNNER_AUDIO_RECORDER_H_
#define RUNNER_AUDIO_RECORDER_H_

#include <windows.h>

#include <atomic>
#include <cstdint>
#include <future>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

// Records the system's default capture endpoint into a 16 kHz mono 16-bit WAV
// using WASAPI (Windows Core Audio).
//
// Why this and not the obvious choices:
//   * The `record` plugin's Windows backend goes through Media Foundation and
//     takes an access violation inside MFPlat.DLL on this machine, which kills
//     the process before Dart can see anything.
//   * waveIn (MME) opens the device happily but never delivers a single buffer
//     for this USB microphone, so every clip came back empty.
//   * WASAPI is the layer Windows itself records through, is not Media
//     Foundation, and GetDefaultAudioEndpoint(eCapture) *is* "whatever the OS
//     considers the default microphone" -- exactly the decoupling we want: the
//     app never enumerates or picks a device.
//
// All WASAPI calls happen on one dedicated thread. That is not tidiness: the
// window thread runs an STA, and handing an interface obtained there to another
// thread without marshalling happens to work once and then silently yields no
// audio. Keeping every Core Audio call on the capture thread avoids the
// apartment entirely.
class AudioRecorder {
 public:
  AudioRecorder() = default;
  ~AudioRecorder();

  AudioRecorder(const AudioRecorder&) = delete;
  AudioRecorder& operator=(const AudioRecorder&) = delete;

  bool Start(const std::string& path, std::string* error);
  bool Stop(std::string* error);
  void Cancel();

  // Returns the PCM captured since the previous call, so the UI can stream it to
  // the live transcription preview without recording twice. The WAV written by
  // Stop() still contains the whole clip.
  std::vector<uint8_t> ReadChunk();

  bool recording() const { return recording_.load(); }

 private:
  void ThreadMain(std::promise<std::string> ready);
  bool WriteWav(std::string* error);
  static std::string HResultMessage(long code);

  std::thread thread_;
  std::atomic<bool> running_{false};
  std::atomic<bool> recording_{false};

  std::mutex mutex_;
  std::vector<uint8_t> pcm_;
  size_t read_offset_ = 0;
  std::string path_;
};

#endif  // RUNNER_AUDIO_RECORDER_H_
