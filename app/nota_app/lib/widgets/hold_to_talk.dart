import 'package:flutter/material.dart';

/// 按住说话按钮：长按开始录音，松手结束并上传。
class HoldToTalkButton extends StatefulWidget {
  const HoldToTalkButton({
    super.key,
    required this.recording,
    required this.enabled,
    required this.onStart,
    required this.onStop,
  });

  final bool recording;
  final bool enabled;
  final VoidCallback onStart;
  final VoidCallback onStop;

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
          return AnimatedContainer(
            duration: const Duration(milliseconds: 120),
            width: 160,
            height: 160,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: const Color(0xFF1D212A),
              border: Border.all(color: activeColor, width: 2),
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
                Icon(widget.recording ? Icons.stop : Icons.mic, size: 44, color: activeColor),
                const SizedBox(height: 10),
                Text(
                  widget.recording ? '松手结束' : '按住说话',
                  style: TextStyle(fontSize: 14, color: activeColor),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
