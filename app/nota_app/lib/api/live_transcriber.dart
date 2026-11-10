import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

/// Live transcription preview over `/ws/transcribe`.
///
/// This is a hint, not the transcript: it sends raw 16 kHz mono PCM as it is
/// captured and surfaces partial text while the user is still talking. The
/// authoritative transcript is produced by the backend from the uploaded file,
/// so losing this connection only means losing the preview.
class LiveTranscriber {
  LiveTranscriber({required String baseUrl})
      : _url = _wsUrl(baseUrl);

  final String _url;
  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;

  /// Latest partial text. Null until the first partial arrives.
  final ValueNotifier<String> text = ValueNotifier<String>('');
  final ValueNotifier<bool> available = ValueNotifier<bool>(false);

  static String _wsUrl(String baseUrl) {
    final trimmed = baseUrl.endsWith('/')
        ? baseUrl.substring(0, baseUrl.length - 1)
        : baseUrl;
    if (trimmed.startsWith('https://')) {
      return 'wss://${trimmed.substring(8)}/ws/transcribe';
    }
    if (trimmed.startsWith('http://')) {
      return 'ws://${trimmed.substring(7)}/ws/transcribe';
    }
    return 'ws://$trimmed/ws/transcribe';
  }

  Future<void> connect() async {
    try {
      final channel = WebSocketChannel.connect(Uri.parse(_url));
      _channel = channel;
      _subscription = channel.stream.listen(
        (dynamic message) {
          if (message is! String) return;
          // Kept deliberately dumb: the server owns the protocol, and an
          // unexpected message should be ignored rather than crash recording.
          if (message.contains('"type":"ready"')) {
            available.value = true;
          } else if (message.contains('"type":"partial"')) {
            final value = _field(message, 'text');
            if (value != null) text.value = value;
          }
        },
        onError: (_) {},
        onDone: () {},
        cancelOnError: false,
      );
      await channel.ready;
      available.value = true;
    } catch (_) {
      available.value = false;
    }
  }

  /// Sends a chunk of 16-bit PCM. Failures are ignored: the preview is optional
  /// and must never interrupt a recording.
  void send(List<int> pcm) {
    if (pcm.isEmpty) return;
    try {
      _channel?.sink.add(pcm);
    } catch (_) {
      // Channel closed mid-recording; nothing useful to do.
    }
  }

  Future<void> close() async {
    try {
      await _subscription?.cancel();
      await _channel?.sink.close();
    } catch (_) {
      // Already closed.
    }
    _subscription = null;
    _channel = null;
    available.value = false;
  }

  void dispose() {
    unawaited(close());
    text.dispose();
    available.dispose();
  }

  /// Minimal extraction of a string field, to avoid depending on a particular
  /// JSON layout in the preview path.
  static String? _field(String json, String name) {
    final key = '"$name":';
    final start = json.indexOf(key);
    if (start < 0) return null;
    var index = start + key.length;
    while (index < json.length && json[index] == ' ') {
      index++;
    }
    if (index >= json.length || json[index] != '"') return null;
    index++;
    final buffer = StringBuffer();
    while (index < json.length && json[index] != '"') {
      if (json[index] == r'\' && index + 1 < json.length) {
        index++;
        buffer.write(json[index]);
      } else {
        buffer.write(json[index]);
      }
      index++;
    }
    return buffer.toString();
  }
}
