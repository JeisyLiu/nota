import 'package:flutter/material.dart';

/// 顶部额度条：颜色随剩余量变化，用完后变橙，和后端 429 的语义一致。
class QuotaBar extends StatelessWidget {
  const QuotaBar({super.key, required this.used, required this.limit});

  final int used;
  final int? limit;

  @override
  Widget build(BuildContext context) {
    final unlimited = limit == null;
    final ratio = unlimited ? 0.1 : (limit == 0 ? 0.0 : used / limit!);
    final full = !unlimited && used >= limit!;
    final color = full
        ? const Color(0xFFFBBF24)
        : const Color(0xFF6EE7B7);

    return Row(
      children: [
        const Text('今日额度', style: TextStyle(fontSize: 12, color: Color(0xFF8B93A7))),
        const SizedBox(width: 8),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(99),
            child: LinearProgressIndicator(
              value: ratio.clamp(0.0, 1.0),
              minHeight: 8,
              backgroundColor: const Color(0xFF22262F),
              valueColor: AlwaysStoppedAnimation<Color>(color),
            ),
          ),
        ),
        const SizedBox(width: 8),
        Text(
          unlimited ? '$used / 不限' : '$used / $limit',
          style: const TextStyle(
            fontSize: 12,
            color: Color(0xFF8B93A7),
            fontFeatures: [FontFeature.tabularFigures()],
          ),
        ),
      ],
    );
  }
}
