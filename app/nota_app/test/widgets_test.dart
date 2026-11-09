import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nota_app/widgets/hold_to_talk.dart';
import 'package:nota_app/widgets/quota_bar.dart';
import 'package:nota_app/widgets/state_badge.dart';

/// 组件级测试：不联网，只验证渲染与交互契约。
///
/// 注意：录音按钮带一个无限脉冲动画，所以这里一律用 `pump(duration)` 推进，
/// 不能用 `pumpAndSettle()` —— 那会一直等不到"静止"。
void main() {
  Widget wrap(Widget child) => MaterialApp(
        theme: ThemeData(brightness: Brightness.dark),
        home: Scaffold(body: Center(child: child)),
      );

  testWidgets('HoldToTalkButton 长按开始、松手结束', (tester) async {
    var started = 0;
    var stopped = 0;

    await tester.pumpWidget(wrap(HoldToTalkButton(
      recording: false,
      enabled: true,
      onStart: () => started++,
      onStop: () => stopped++,
    )));
    expect(find.text('按住说话'), findsOneWidget);
    expect(find.byIcon(Icons.mic), findsOneWidget);

    final gesture = await tester.startGesture(tester.getCenter(find.byType(HoldToTalkButton)));
    await tester.pump(const Duration(milliseconds: 600));
    expect(started, 1);

    await gesture.up();
    await tester.pump(const Duration(milliseconds: 100));
    expect(stopped, 1);
  });

  testWidgets('HoldToTalkButton 录音中显示松手结束', (tester) async {
    await tester.pumpWidget(wrap(HoldToTalkButton(
      recording: true,
      enabled: true,
      onStart: () {},
      onStop: () {},
    )));
    await tester.pump(const Duration(milliseconds: 50));
    expect(find.text('松手结束'), findsOneWidget);
    expect(find.byIcon(Icons.stop), findsOneWidget);
  });

  testWidgets('HoldToTalkButton 禁用时不触发回调', (tester) async {
    var started = 0;
    await tester.pumpWidget(wrap(HoldToTalkButton(
      recording: false,
      enabled: false,
      onStart: () => started++,
      onStop: () {},
    )));
    final gesture = await tester.startGesture(tester.getCenter(find.byType(HoldToTalkButton)));
    await tester.pump(const Duration(milliseconds: 600));
    await gesture.up();
    await tester.pump(const Duration(milliseconds: 100));
    expect(started, 0);
  });

  testWidgets('QuotaBar 在额度用尽时显示已用数量', (tester) async {
    await tester.pumpWidget(wrap(const QuotaBar(used: 10, limit: 10)));
    expect(find.text('10 / 10'), findsOneWidget);
    expect(find.text('今日额度'), findsOneWidget);
  });

  testWidgets('QuotaBar 对 Pro 用户显示不限', (tester) async {
    await tester.pumpWidget(wrap(const QuotaBar(used: 42, limit: null)));
    expect(find.text('42 / 不限'), findsOneWidget);
  });

  testWidgets('StateBadge 把后端状态翻成中文', (tester) async {
    for (final entry in {
      'queued': '排队中',
      'transcribing': '转写中',
      'agent_running': '整理中',
      'done': '已完成',
      'failed': '失败',
    }.entries) {
      await tester.pumpWidget(wrap(StateBadge(state: entry.key)));
      expect(find.text(entry.value), findsOneWidget,
          reason: 'state ${entry.key} should render as ${entry.value}');
    }
  });
}
