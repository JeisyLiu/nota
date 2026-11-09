import 'package:cross_file/cross_file.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../api/nota_client.dart';
import '../models/models.dart';
import '../widgets/hold_to_talk.dart';
import '../widgets/quota_bar.dart';
import '../widgets/state_badge.dart';
import 'detail_page.dart';

class HomePage extends StatefulWidget {
  const HomePage({super.key, required this.client});

  final NotaClient client;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final _recorder = AudioRecorder();
  final _textController = TextEditingController();

  Quota? _quota;
  List<NoteSummary> _notes = const [];
  String? _status;
  bool _recording = false;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _recorder.dispose();
    _textController.dispose();
    widget.client.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final quota = await widget.client.quota();
      final notes = await widget.client.notes();
      if (!mounted) return;
      setState(() {
        _quota = quota;
        _notes = notes;
      });
    } catch (e) {
      _toast(_message(e), error: true);
    }
  }

  String _message(Object e) => switch (e) {
        QuotaExceeded q => q.toString(),
        NotaApiError a => a.message,
        _ => e.toString(),
      };

  void _toast(String text, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(text),
      backgroundColor: error ? const Color(0xFF3A1F22) : const Color(0xFF1D212A),
      behavior: SnackBarBehavior.floating,
    ));
  }

  // ------------------------------------------------------------------ 录音
  Future<void> _startRecording() async {
    if (!await _recorder.hasPermission()) {
      _toast('没有麦克风权限', error: true);
      return;
    }
    // Web 上没有临时目录，record 会忽略 path 并返回一个 blob URL。
    final path = kIsWeb
        ? ''
        : '${(await getTemporaryDirectory()).path}/nota-'
            '${DateTime.now().millisecondsSinceEpoch}.m4a';
    await _recorder.start(const RecordConfig(encoder: AudioEncoder.aacLc), path: path);
    setState(() {
      _recording = true;
      _status = '正在录音…';
    });
  }

  Future<void> _stopRecording() async {
    final path = await _recorder.stop();
    setState(() {
      _recording = false;
      _status = '上传中…';
    });
    if (path == null) {
      setState(() => _status = null);
      return;
    }
    // 用 cross_file 读字节：移动端读本地文件，Web 端直接 fetch blob URL。
    final bytes = await XFile(path).readAsBytes();
    await _submit(() => widget.client.sendAudio(bytes, filename: 'note.m4a'));
  }

  Future<void> _sendText() async {
    final text = _textController.text.trim();
    if (text.isEmpty) return;
    _textController.clear();
    await _submit(() => widget.client.sendText(text));
  }

  Future<void> _submit(Future<Map<String, dynamic>> Function() submit) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _status ??= '提交中…';
    });
    try {
      final created = await submit();
      final jobId = created['job_id'] as String;
      setState(() => _status = 'Agent 正在处理…');
      final job = await widget.client.waitFor(jobId);
      if (!mounted) return;
      setState(() {
        _status = null;
        _busy = false;
      });
      await _refresh();
      if (!mounted) return;
      if (job.state == 'failed') {
        _toast('处理失败：${job.error ?? '未知错误'}（额度已退回）', error: true);
      } else {
        await Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => DetailPage(client: widget.client, jobId: jobId),
        ));
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _status = null;
      });
      _toast(_message(e), error: true);
      await _refresh();
    }
  }

  Future<void> _openNote(NoteSummary note) async {
    try {
      final job = await widget.client.jobForNote(note.noteId);
      if (!mounted) return;
      await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => DetailPage(client: widget.client, jobId: job.jobId),
      ));
    } catch (e) {
      _toast(_message(e), error: true);
    }
  }

  // ------------------------------------------------------------------ UI
  @override
  Widget build(BuildContext context) {
    final quota = _quota;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: const Color(0xFF101318),
        title: Row(
          children: [
            Container(
              width: 10,
              height: 10,
              decoration: const BoxDecoration(
                color: Color(0xFF6EE7B7),
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 10),
            const Text('Nota', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(width: 10),
            const Text('随口说，剩下的我来',
                style: TextStyle(fontSize: 11, color: Color(0xFF8B93A7))),
          ],
        ),
        actions: [
          IconButton(
            onPressed: _busy ? null : _refresh,
            icon: const Icon(Icons.refresh),
            tooltip: '刷新',
          ),
        ],
      ),
      body: Column(
        children: [
          // Quota stays pinned at the top: it is read-only status, and keeping it
          // outside the scroll view means it is visible while recording, when
          // "am I about to run out" actually matters.
          if (quota != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: QuotaBar(used: quota.used, limit: quota.limit),
            ),
          Expanded(child: _buildNoteList()),
          _buildBottomBar(quota),
        ],
      ),
    );
  }

  Widget _buildNoteList() {
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
        children: [
          const Text('最近',
              style: TextStyle(fontSize: 13, color: Color(0xFF8B93A7), letterSpacing: 1)),
          const SizedBox(height: 8),
          if (_notes.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 24),
              child: Center(
                child: Text('还没有记录，说一句话试试',
                    style: TextStyle(color: Color(0xFF8B93A7))),
              ),
            )
          else
            ..._notes.map((note) => Card(
                  child: ListTile(
                    title: Text(
                      note.title ?? note.transcript,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 14),
                    ),
                    subtitle: Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Row(
                        children: [
                          if (note.state != null) StateBadge(state: note.state!),
                          const SizedBox(width: 8),
                          Text(
                            note.createdAt.replaceFirst('T', ' ').split('+').first,
                            style: const TextStyle(fontSize: 11, color: Color(0xFF8B93A7)),
                          ),
                        ],
                      ),
                    ),
                    trailing: const Icon(Icons.chevron_right, size: 18),
                    onTap: () => _openNote(note),
                  ),
                )),
        ],
      ),
    );
  }

  /// Bottom action area: hold-to-talk plus the keyboard alternative.
  ///
  /// Both live here rather than in the scrolling list because this is a
  /// thumb-reach problem, not a layout preference — the primary action has to be
  /// where the thumb already rests, and it must not scroll away while the user
  /// is reading older notes. The gradient + top hairline separate it from the
  /// list so the button reads as anchored rather than floating.
  Widget _buildBottomBar(Quota? quota) {
    final exhausted = quota != null && quota.exhausted;
    return Container(
      decoration: const BoxDecoration(
        color: Color(0xFF101318),
        border: Border(top: BorderSide(color: Color(0xFF22262F))),
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 10),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (_status != null) ...[
                Text(_status!,
                    style: const TextStyle(fontSize: 12, color: Color(0xFF8B93A7))),
                const SizedBox(height: 8),
              ],
              if (exhausted) ...[
                const Text('今日免费额度已用完，本地时间零点重置',
                    style: TextStyle(fontSize: 12, color: Color(0xFFFBBF24))),
                const SizedBox(height: 8),
              ],
              HoldToTalkButton(
                recording: _recording,
                enabled: !_busy && !exhausted,
                onStart: _startRecording,
                onStop: _stopRecording,
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _textController,
                      enabled: !_busy,
                      maxLines: 2,
                      minLines: 1,
                      style: const TextStyle(fontSize: 13),
                      decoration: const InputDecoration(
                        hintText: '也可以直接打字…',
                        hintStyle: TextStyle(fontSize: 13),
                        isDense: true,
                        contentPadding: EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                      ),
                      onSubmitted: (_) => _sendText(),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    onPressed: _busy ? null : _sendText,
                    icon: const Icon(Icons.arrow_upward, size: 20),
                    tooltip: '发送',
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
