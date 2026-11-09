import 'package:flutter/material.dart';

/// 按住说话按钮：长按开始录音，松手结束并上传。
///
/// 尺寸可调是因为它现在住在底部固定栏里：底部留给它的高度有限，而它又是
/// 最主要的操作，所以按「刚好够拇指按住、又不太占屏」来定，而不是铺满。
class HoldToTalkButton extends StatefulWidget {
  const HoldToTalkButton({
    super.key,
    required this.recording,
    required this.enabled,
    required this.onStart,
    required this.onStop,
    this.size = 120,
  });

  final bool recording;
  final bool enabled;
  final VoidCallback onStart;
  final VoidCallback onStop;
  final double size;

  @override
  State<HoldToTalkButton> createState() => _HoldToTalkButtonState();
}

class _HoldToTalkButtonState extends State<HoldToTalkButton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final activeColor = widget.recording
        ? const Color(0xFFF87171)
        : Theme.of(context).colorScheme.primary;

    return GestureDetector(
      onLongPressStart: widget.enabled ? (_) => widget.onStart() : null,
      onLongPressEnd: widget.enabled ? (_) => widget.onStop() : null,
      onLongPressCancel: widget.enabled ? () => widget.onStop() : null,
      child: AnimatedBuilder(
        animation: _pulse,
        builder: (context, _) {
          final glow = widget.recording ? 6 + 14 * _pulse.value : 0.0;
          final s = widget.size;
          return AnimatedContainer(
            duration: const Duration(milliseconds: 120),
            width: s,
            height: s,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: const Color(0xFF1D212A),
              border: Border.all(
                color: widget.enabled ? activeColor : const Color(0xFF2A2F3A),
                width: 2,
              ),
              boxShadow: [
                BoxShadow(
                  color: activeColor.withValues(alpha: widget.recording ? 0.28 : 0.08),
                  blurRadius: glow,
                  spreadRadius: glow / 3,
                ),
              ],
            ),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(
                  widget.recording ? Icons.stop : Icons.mic,
                  size: s * 0.34,
                  color: widget.enabled ? activeColor : const Color(0xFF5A6272),
                ),
                SizedBox(height: s * 0.06),
                Text(
                  widget.recording ? '松手结束' : '按住说话',
                  style: TextStyle(
                    fontSize: s * 0.105,
                    color: widget.enabled ? activeColor : const Color(0xFF5A6272),
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
