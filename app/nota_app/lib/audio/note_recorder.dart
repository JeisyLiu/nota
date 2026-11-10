import 'package:flutter/foundation.dart'
    show TargetPlatform, defaultTargetPlatform, kIsWeb;
import 'package:flutter/services.dart';
import 'package:record/record.dart';

/// Thrown when the microphone cannot be opened at all. Carries a message that is
/// safe to show to the user.
class RecorderUnavailable implements Exception {
  RecorderUnavailable(this.message);
  final String message;
  @override
  String toString() => message;
}

/// What the UI needs from a microphone, and nothing else.
///
/// Two implementations exist because the platforms genuinely differ:
///
///  * [PluginNoteRecorder] wraps the `record` plugin, used on Android, iOS and
///    web.
///  * [WindowsNoteRecorder] talks to a small native recorder in the Windows
///    runner. The `record` plugin's Windows backend goes through Media
///    Foundation and takes an access violation inside MFPlat.DLL here, killing
///    the process; the native one uses waveIn (MME) instead and never touches
///    Media Foundation.
///
/// The screen only sees this interface, so it has no idea which mechanism it is
/// talking to -- and in particular never enumerates or chooses a device itself.
/// Device selection belongs to the operating system in both cases.
abstract class NoteRecorder {
  /// Extension of the file this recorder produces, so the upload can carry a
  /// truthful filename.
  String get fileExtension;

  Future<bool> hasPermission();

  /// Starts capturing into [path] (ignored on web, where the plugin returns a
  /// blob URL instead).
  Future<void> start(String path);

  /// Stops and returns the finished file path, or null when nothing usable was
  /// captured.
  Future<String?> stop();

  Future<void> cancel();

  void dispose();
}

/// True when the platform needs the runner's own recorder.
bool get usesNativeWindowsRecorder =>
    !kIsWeb && defaultTargetPlatform == TargetPlatform.windows;

NoteRecorder createNoteRecorder() =>
    usesNativeWindowsRecorder ? WindowsNoteRecorder() : PluginNoteRecorder();

/// The `record` plugin: Android, iOS and web.
class PluginNoteRecorder implements NoteRecorder {
  final AudioRecorder _recorder = AudioRecorder();

  @override
  String get fileExtension => 'm4a';

  @override
  Future<bool> hasPermission() => _recorder.hasPermission();

  @override
  Future<void> start(String path) async {
    // 16 kHz mono is what the transcription pipeline wants anyway, and AAC keeps
    // the upload small.
    await _recorder.start(
      const RecordConfig(
        encoder: AudioEncoder.aacLc,
        sampleRate: 16000,
        numChannels: 1,
      ),
      path: path,
    );
  }

  @override
  Future<String?> stop() => _recorder.stop();

  @override
  Future<void> cancel() async {
    try {
      await _recorder.cancel();
    } catch (_) {
      // Cancelling a recorder that never started is not an error worth showing.
    }
  }

  @override
  void dispose() => _recorder.dispose();
}

/// Windows: the native waveIn recorder registered on the `nota/audio` channel.
class WindowsNoteRecorder implements NoteRecorder {
  static const MethodChannel _channel = MethodChannel('nota/audio');

  String? _path;

  @override
  String get fileExtension => 'wav';

  @override
  Future<bool> hasPermission() async {
    // MME has no permission prompt; if the device cannot be opened, start()
    // reports it. Returning true here keeps the UI from claiming a permission
    // problem that does not exist.
    return true;
  }

  @override
  Future<void> start(String path) async {
    try {
      await _channel.invokeMethod<bool>('start', <String, Object>{'path': path});
      _path = path;
    } on PlatformException catch (e) {
      throw RecorderUnavailable(e.message ?? '无法打开录音设备');
    } on MissingPluginException {
      throw RecorderUnavailable('Windows 录音组件未加载');
    }
  }

  @override
  Future<String?> stop() async {
    try {
      await _channel.invokeMethod<bool>('stop');
      return _path;
    } on PlatformException catch (e) {
      throw RecorderUnavailable(e.message ?? '录音失败');
    } finally {
      _path = null;
    }
  }

  @override
  Future<void> cancel() async {
    try {
      await _channel.invokeMethod<void>('cancel');
    } catch (_) {
      // Nothing recorded, nothing to clean up.
    }
    _path = null;
  }

  @override
  void dispose() {
    // The native side releases the device when the window closes.
  }
}
