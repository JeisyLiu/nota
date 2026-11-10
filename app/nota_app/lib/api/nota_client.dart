/// 后端 HTTP 客户端 —— 唯一与协议耦合的地方。
///
/// 所有请求带 `X-User-Id`；MVP 没有登录，用户由这个头决定。
/// 429 会被翻译成 [QuotaExceeded]，让 UI 能显示"今日额度已用完"而不是通用错误。
///
/// 这里刻意不 import `dart:io`：音频以字节形式传入，读文件的事交给调用方
/// （`cross_file` 在移动端和 Web 上都能读），所以同一份客户端代码能在
/// Android 与浏览器里跑。
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models/models.dart';

class NotaApiError implements Exception {
  const NotaApiError(this.message, {this.status = 0});
  final String message;
  final int status;

  @override
  String toString() => message;
}

class NotaClient {
  NotaClient({required this.baseUrl, this.userId = 'demo', http.Client? client})
      : _client = client ?? http.Client();

  final String baseUrl;
  final String userId;
  final http.Client _client;

  Map<String, String> get _headers => {'X-User-Id': userId};

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Never _fail(http.Response res) {
    String message = 'HTTP ${res.statusCode}';
    Map<String, dynamic>? detail;
    try {
      final decoded = jsonDecode(utf8.decode(res.bodyBytes));
      detail = decoded is Map<String, dynamic> ? decoded['detail'] as Map<String, dynamic>? : null;
      if (detail != null) message = (detail['message'] ?? detail['error'] ?? message).toString();
    } catch (_) {
      // 后端异常时可能不是 JSON
    }
    if (res.statusCode == 429 && detail != null) {
      throw QuotaExceeded(
        used: (detail['used'] as num?)?.toInt() ?? 0,
        limit: (detail['limit'] as num?)?.toInt() ?? 0,
        resetAt: detail['reset_at'] as String? ?? '',
      );
    }
    throw NotaApiError(message, status: res.statusCode);
  }

  Future<Map<String, dynamic>> _getJson(String path) async {
    final res = await _client.get(_uri(path), headers: _headers);
    if (res.statusCode != 200) _fail(res);
    return jsonDecode(utf8.decode(res.bodyBytes)) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> _postJson(String path, Map<String, dynamic> body) async {
    final res = await _client.post(
      _uri(path),
      headers: {..._headers, 'Content-Type': 'application/json'},
      body: jsonEncode(body),
    );
    if (res.statusCode != 200) _fail(res);
    return jsonDecode(utf8.decode(res.bodyBytes)) as Map<String, dynamic>;
  }

  // ------------------------------------------------------------------ 额度
  Future<Quota> quota() async => Quota.fromJson(await _getJson('/api/quota'));

  // ------------------------------------------------------------------ 提交
  Future<Map<String, dynamic>> sendText(String text, {String? idempotencyKey}) =>
      _postJson('/api/notes/text', {
        'text': text,
        if (idempotencyKey != null) 'idempotency_key': idempotencyKey,
      });

  /// 录音上传。音频是 m4a/aac，服务端用 ffmpeg 转 wav 再送 whisper。
  ///
  /// 收字节而不是 `File`，这样客户端不依赖 `dart:io`，Web 端也能复用。
  Future<Map<String, dynamic>> sendAudio(
    Uint8List bytes, {
    String filename = 'note.m4a',
    String? idempotencyKey,
  }) async {
    final request = http.MultipartRequest('POST', _uri('/api/notes/audio'))
      ..headers.addAll(_headers)
      ..files.add(http.MultipartFile.fromBytes(
        'file',
        bytes,
        filename: filename,
        contentType: _mediaType(filename),
      ));
    if (idempotencyKey != null) {
      request.fields['idempotency_key'] = idempotencyKey;
    }
    final streamed = await _client.send(request);
    final res = await http.Response.fromStream(streamed);
    if (res.statusCode != 200) _fail(res);
    return jsonDecode(utf8.decode(res.bodyBytes)) as Map<String, dynamic>;
  }

  static http.MediaType? _mediaType(String path) {
    final lower = path.toLowerCase();
    if (lower.endsWith('.m4a') || lower.endsWith('.aac')) {
      return http.MediaType('audio', 'mp4');
    }
    if (lower.endsWith('.wav')) return http.MediaType('audio', 'wav');
    if (lower.endsWith('.webm')) return http.MediaType('audio', 'webm');
    return null;
  }

  // ------------------------------------------------------------------ 查询
  Future<NoteJob> job(String jobId) async =>
      NoteJob.fromJson(await _getJson('/api/jobs/$jobId'));

  Future<NoteJob> jobForNote(String noteId) async =>
      NoteJob.fromJson(await _getJson('/api/notes/$noteId/job'));

  /// Stops a queued or running job. The backend refunds the reserved request, so
  /// the user does not pay for work they cancelled.
  Future<NoteJob> cancel(String jobId) async {
    final res = await _client.post(_uri('/api/jobs/$jobId/cancel'), headers: _headers);
    if (res.statusCode != 200) _fail(res);
    final body = jsonDecode(utf8.decode(res.bodyBytes)) as Map<String, dynamic>;
    return NoteJob.fromJson(body['job'] as Map<String, dynamic>);
  }

  Future<List<NoteSummary>> notes({int limit = 30}) async {
    final res = await _client.get(_uri('/api/notes?limit=$limit'), headers: _headers);
    if (res.statusCode != 200) _fail(res);
    final list = jsonDecode(utf8.decode(res.bodyBytes)) as List<dynamic>;
    return list
        .map((e) => NoteSummary.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<List<AgentStep>> steps(String jobId) async {
    final res = await _client.get(_uri('/api/jobs/$jobId/steps'), headers: _headers);
    if (res.statusCode != 200) _fail(res);
    final list = jsonDecode(utf8.decode(res.bodyBytes)) as List<dynamic>;
    return list
        .map((e) => AgentStep.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<List<Todo>> todos({String? status}) async {
    final path = status == null ? '/api/todos' : '/api/todos?status=$status';
    final res = await _client.get(_uri(path), headers: _headers);
    if (res.statusCode != 200) _fail(res);
    final list = jsonDecode(utf8.decode(res.bodyBytes)) as List<dynamic>;
    return list.map((e) => Todo.fromJson(e as Map<String, dynamic>)).toList(growable: false);
  }

  Future<void> completeTodo(String todoId) async {
    final res = await _client.post(_uri('/api/todos/$todoId/done'), headers: _headers);
    if (res.statusCode != 200) _fail(res);
  }

  Future<List<PendingAction>> pendingActions() async {
    final res = await _client.get(_uri('/api/actions'), headers: _headers);
    if (res.statusCode != 200) _fail(res);
    final list = jsonDecode(utf8.decode(res.bodyBytes)) as List<dynamic>;
    return list
        .map((e) => PendingAction.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<Map<String, dynamic>> decide(String actionId, bool approve) =>
      _postJson('/api/actions/$actionId', {'approve': approve});

  Future<AskResult> ask(String question) async =>
      AskResult.fromJson(await _postJson('/api/ask', {'question': question}));

  /// 轮询到终态。后端有 SSE，但移动端为省电用轮询更简单，1s 一次。
  Future<NoteJob> waitFor(String jobId,
      {Duration timeout = const Duration(seconds: 60)}) async {
    final deadline = DateTime.now().add(timeout);
    while (DateTime.now().isBefore(deadline)) {
      final job = await this.job(jobId);
      if (job.finished) return job;
      await Future<void>.delayed(const Duration(milliseconds: 900));
    }
    return job(jobId);
  }

  void dispose() => _client.close();
}
