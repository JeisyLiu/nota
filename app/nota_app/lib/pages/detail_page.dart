import 'dart:convert';

import 'package:flutter/material.dart';

import '../api/nota_client.dart';
import '../models/models.dart';
import '../widgets/state_badge.dart';

/// 单条记录：摘要 + 待办 / 提醒 + Agent Trace + 待确认动作。
class DetailPage extends StatefulWidget {
  const DetailPage({super.key, required this.client, required this.jobId});

  final NotaClient client;
  final String jobId;

  @override
  State<DetailPage> createState() => _DetailPageState();
}

class _DetailPageState extends State<DetailPage> {
  NoteJob? _job;
  List<AgentStep> _steps = const [];
  List<PendingAction> _actions = const [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final job = await widget.client.job(widget.jobId);
      final steps = await widget.client.steps(widget.jobId);
      final actions = await widget.client.pendingActions();
      if (!mounted) return;
      setState(() {
        _job = job;
        _steps = steps;
        _actions = actions;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('加载失败：$e')));
    }
  }

  Future<void> _complete(Todo todo) async {
    try {
      await widget.client.completeTodo(todo.id);
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  Future<void> _decide(PendingAction action, bool approve) async {
    try {
      await widget.client.decide(action.id, approve);
      await _load();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(approve ? '已执行' : '已忽略')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final job = _job;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: const Color(0xFF101318),
        title: const Text('整理结果', style: TextStyle(fontSize: 16)),
        actions: [
          IconButton(onPressed: _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : job == null
              ? const Center(child: Text('记录不存在'))
              : ListView(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
                  children: [
                    Row(
                      children: [
                        StateBadge(state: job.state),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            job.jobId,
                            style: const TextStyle(
                                fontSize: 11,
                                color: Color(0xFF8B93A7),
                                fontFamily: 'monospace'),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                    if (job.title != null) ...[
                      const SizedBox(height: 16),
                      Text(job.title!,
                          style: const TextStyle(
                              fontSize: 18, fontWeight: FontWeight.w600)),
                    ],
                    const SizedBox(height: 10),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(
                        color: const Color(0xFF1D212A),
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: const Color(0xFF2A2F3A)),
                      ),
                      child: Text(
                        job.summary ??
                            (job.state == 'failed'
                                ? '处理失败：${job.error ?? '未知错误'}（本次额度已退回）'
                                : '正在处理…'),
                        style: const TextStyle(fontSize: 14, height: 1.6),
                      ),
                    ),
                    if (_actions.isNotEmpty) ...[
                      const SizedBox(height: 20),
                      const Text('需要你确认',
                          style: TextStyle(
                              fontSize: 13, color: Color(0xFFFBBF24), letterSpacing: 1)),
                      const SizedBox(height: 8),
                      ..._actions.map((a) => Card(
                            color: const Color(0xFF2A2416),
                            child: Padding(
                              padding: const EdgeInsets.all(12),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text('${a.tool}（不可撤销）',
                                      style: const TextStyle(
                                          fontSize: 13, color: Color(0xFFFBBF24))),
                                  const SizedBox(height: 6),
                                  Text(
                                    const JsonEncoder.withIndent('  ').convert(a.args),
                                    style: const TextStyle(
                                        fontSize: 11,
                                        color: Color(0xFF8B93A7),
                                        fontFamily: 'monospace'),
                                  ),
                                  const SizedBox(height: 10),
                                  Row(
                                    children: [
                                      FilledButton(
                                        onPressed: () => _decide(a, true),
                                        child: const Text('确认执行'),
                                      ),
                                      const SizedBox(width: 8),
                                      OutlinedButton(
                                        onPressed: () => _decide(a, false),
                                        child: const Text('忽略'),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          )),
                    ],
                    const SizedBox(height: 20),
                    const Text('待办',
                        style: TextStyle(
                            fontSize: 13, color: Color(0xFF8B93A7), letterSpacing: 1)),
                    const SizedBox(height: 6),
                    if (job.todos.isEmpty)
                      const Text('暂无待办',
                          style: TextStyle(fontSize: 13, color: Color(0xFF8B93A7)))
                    else
                      ...job.todos.map((todo) => Card(
                            child: CheckboxListTile(
                              value: todo.done,
                              onChanged: (_) => _complete(todo),
                              dense: true,
                              controlAffinity: ListTileControlAffinity.leading,
                              title: Text(
                                todo.title,
                                style: TextStyle(
                                  fontSize: 14,
                                  decoration:
                                      todo.done ? TextDecoration.lineThrough : null,
                                  color: todo.done
                                      ? const Color(0xFF8B93A7)
                                      : const Color(0xFFE6E8EE),
                                ),
                              ),
                              subtitle: todo.dueAt == null
                                  ? null
                                  : Text(_humanTime(todo.dueAt!),
                                      style: const TextStyle(
                                          fontSize: 11, color: Color(0xFF8B93A7))),
                            ),
                          )),
                    const SizedBox(height: 20),
                    const Text('提醒',
                        style: TextStyle(
                            fontSize: 13, color: Color(0xFF8B93A7), letterSpacing: 1)),
                    const SizedBox(height: 6),
                    if (job.reminders.isEmpty)
                      const Text('暂无提醒',
                          style: TextStyle(fontSize: 13, color: Color(0xFF8B93A7)))
                    else
                      ...job.reminders.map((rem) => Card(
                            child: ListTile(
                              dense: true,
                              leading: const Icon(Icons.notifications_none, size: 18),
                              title: Text(rem.title, style: const TextStyle(fontSize: 14)),
                              subtitle: Text(_humanTime(rem.fireAt),
                                  style: const TextStyle(
                                      fontSize: 11, color: Color(0xFF8B93A7))),
                            ),
                          )),
                    const SizedBox(height: 20),
                    const Text('Agent Trace',
                        style: TextStyle(
                            fontSize: 13, color: Color(0xFF8B93A7), letterSpacing: 1)),
                    const SizedBox(height: 8),
                    ..._steps.map(_buildStep),
                  ],
                ),
    );
  }

  Widget _buildStep(AgentStep step) {
    final isTool = step.type == 'tool_result';
    final color = isTool ? const Color(0xFF6EE7B7) : const Color(0xFF38BDF8);
    final payload = step.result ?? step.args;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            margin: const EdgeInsets.only(top: 4),
            width: 9,
            height: 9,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(isTool ? 'TOOL' : 'ASSISTANT',
                        style: TextStyle(
                            fontSize: 10, color: color, letterSpacing: 0.6)),
                    const SizedBox(width: 8),
                    Text(step.name ?? '',
                        style: const TextStyle(
                            fontSize: 12, fontFamily: 'monospace')),
                    const Spacer(),
                    if (step.ms > 0)
                      Text('${step.ms} ms',
                          style: const TextStyle(
                              fontSize: 10, color: Color(0xFF8B93A7))),
                  ],
                ),
                if (payload != null) ...[
                  const SizedBox(height: 6),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(9),
                    decoration: BoxDecoration(
                      color: const Color(0xFF10131A),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: const Color(0xFF2A2F3A)),
                    ),
                    child: Text(
                      payload is String
                          ? payload
                          : const JsonEncoder.withIndent('  ').convert(payload),
                      style: const TextStyle(
                          fontSize: 11,
                          height: 1.5,
                          color: Color(0xFFB8C0D4),
                          fontFamily: 'monospace'),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  static String _humanTime(String iso) {
    try {
      final dt = DateTime.parse(iso).toLocal();
      String two(int n) => n.toString().padLeft(2, '0');
      return '${dt.month}/${dt.day} ${two(dt.hour)}:${two(dt.minute)}';
    } catch (_) {
      return iso;
    }
  }
}
