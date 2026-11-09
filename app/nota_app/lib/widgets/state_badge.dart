import 'package:flutter/material.dart';

/// 状态徽章：把后端的 job.state 翻成中文并着色。
class StateBadge extends StatelessWidget {
  const StateBadge({super.key, required this.state});

  final String state;

  static const _labels = {
    'queued': '排队中',
    'transcribing': '转写中',
    'agent_running': '整理中',
    'done': '已完成',
    'failed': '失败',
  };

  @override
  Widget build(BuildContext context) {
    final label = _labels[state] ?? state;
    final Color fg;
    final Color bg;
    switch (state) {
      case 'done':
        fg = const Color(0xFF6EE7B7);
        bg = const Color(0xFF13302A);
      case 'failed':
        fg = const Color(0xFFF87171);
        bg = const Color(0xFF3A1F22);
      default:
        fg = const Color(0xFF38BDF8);
        bg = const Color(0xFF23303F);
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(99)),
      child: Text(label, style: TextStyle(fontSize: 11, color: fg, letterSpacing: 0.4)),
    );
  }
}
