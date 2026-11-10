import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:nota_app/api/nota_client.dart';
import 'package:nota_app/pages/login_page.dart';

/// Builds a client whose HTTP layer is stubbed, so the page can be exercised
/// without a backend.
NotaClient _client(MockClient mock) =>
    NotaClient(baseUrl: 'http://test.local', client: mock);

Future<void> _pump(WidgetTester tester, NotaClient client,
    {ValueChanged<AuthResult>? onLoggedIn, String? notice}) async {
  await tester.pumpWidget(MaterialApp(
    home: LoginPage(
      client: client,
      notice: notice,
      onLoggedIn: onLoggedIn ?? (_) {},
    ),
  ));
  await tester.pump(const Duration(milliseconds: 200));
}

void main() {
  testWidgets('rejects an empty form before touching the network', (tester) async {
    var called = false;
    final client = _client(MockClient((_) async {
      called = true;
      return http.Response('{}', 200);
    }));

    await _pump(tester, client);
    await tester.tap(find.widgetWithText(FilledButton, '登录'));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('用户名至少 2 个字符'), findsOneWidget);
    expect(called, isFalse, reason: 'no request should be sent');
  });

  testWidgets('rejects a short password', (tester) async {
    final client = _client(MockClient((_) async => http.Response('{}', 200)));
    await _pump(tester, client);

    await tester.enterText(find.byType(TextField).first, 'zhangwei');
    await tester.enterText(find.byType(TextField).at(1), '123');
    await tester.tap(find.widgetWithText(FilledButton, '登录'));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('密码至少 6 位'), findsOneWidget);
  });

  testWidgets('shows a friendly message when credentials are wrong', (tester) async {
    final client = _client(MockClient((_) async => http.Response(
          jsonEncode({
            'detail': {'error': 'bad_credentials', 'message': '用户名或密码不正确'}
          }),
          401,
          headers: {'content-type': 'application/json; charset=utf-8'},
        )));

    await _pump(tester, client);
    await tester.enterText(find.byType(TextField).first, 'zhangwei');
    await tester.enterText(find.byType(TextField).at(1), 'wrongpass');
    await tester.tap(find.widgetWithText(FilledButton, '登录'));
    await tester.pump(const Duration(milliseconds: 300));

    expect(find.text('用户名或密码不正确'), findsOneWidget);
  });

  testWidgets('hands the session back on success', (tester) async {
    AuthResult? result;
    final client = _client(MockClient((_) async => http.Response(
          jsonEncode({
            'user': {
              'user_id': 'u_zhangwei',
              'username': 'zhangwei',
              'display_name': '张伟',
              'plan': 'free',
              'role': 'user',
            },
            'token': 'tok-abc',
          }),
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        )));

    await _pump(tester, client, onLoggedIn: (value) => result = value);
    await tester.enterText(find.byType(TextField).first, 'zhangwei');
    await tester.enterText(find.byType(TextField).at(1), '123456');
    await tester.tap(find.widgetWithText(FilledButton, '登录'));
    await tester.pump(const Duration(milliseconds: 300));

    expect(result, isNotNull);
    expect(result!.token, 'tok-abc');
    expect(result!.user.displayName, '张伟');
  });

  testWidgets('the demo button fills the known account', (tester) async {
    final client = _client(MockClient((_) async => http.Response('{}', 200)));
    await _pump(tester, client);

    await tester.tap(find.text('使用演示账号 zhangwei / 123456'));
    await tester.pump(const Duration(milliseconds: 200));

    final fields = tester.widgetList<TextField>(find.byType(TextField)).toList();
    expect(fields[0].controller?.text, 'zhangwei');
    expect(fields[1].controller?.text, '123456');
  });

  testWidgets('register mode asks for confirmation and a nickname', (tester) async {
    final client = _client(MockClient((_) async => http.Response('{}', 200)));
    await _pump(tester, client);

    await tester.tap(find.text('注册'));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('确认密码'), findsOneWidget);
    expect(find.text('昵称（可选）'), findsOneWidget);
    expect(find.text('注册并登录'), findsOneWidget);
  });

  testWidgets('shows the expiry notice passed in by the gate', (tester) async {
    final client = _client(MockClient((_) async => http.Response('{}', 200)));
    await _pump(tester, client, notice: '登录已过期，请重新登录');
    expect(find.text('登录已过期，请重新登录'), findsOneWidget);
  });
  testWidgets('register submits the nickname and returns the session', (tester) async {
    AuthResult? result;
    String? sentBody;
    final client = _client(MockClient((request) async {
      sentBody = request.body;
      return http.Response(
        jsonEncode({
          'user': {
            'user_id': 'u_new',
            'username': 'newbie',
            'display_name': '新同学',
            'plan': 'free',
            'role': 'user',
          },
          'token': 'tok-new',
        }),
        201,
        headers: {'content-type': 'application/json; charset=utf-8'},
      );
    }));

    await _pump(tester, client, onLoggedIn: (value) => result = value);
    await tester.tap(find.text('注册'));
    await tester.pump(const Duration(milliseconds: 200));

    final fields = find.byType(TextField);
    await tester.enterText(fields.at(0), 'newbie');
    await tester.enterText(fields.at(1), '123456');
    await tester.enterText(fields.at(2), '123456');
    await tester.enterText(fields.at(3), '新同学');
    await tester.tap(find.widgetWithText(FilledButton, '注册并登录'));
    await tester.pump(const Duration(milliseconds: 300));

    expect(result?.token, 'tok-new');
    expect(result?.user.displayName, '新同学');
    expect(sentBody, contains('"display_name":"新同学"'));
  });

  testWidgets('register refuses mismatched passwords', (tester) async {
    var called = false;
    final client = _client(MockClient((_) async {
      called = true;
      return http.Response('{}', 201);
    }));

    await _pump(tester, client);
    await tester.tap(find.text('注册'));
    await tester.pump(const Duration(milliseconds: 200));

    final fields = find.byType(TextField);
    await tester.enterText(fields.at(0), 'newbie');
    await tester.enterText(fields.at(1), '123456');
    await tester.enterText(fields.at(2), '654321');
    await tester.tap(find.widgetWithText(FilledButton, '注册并登录'));
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.text('两次输入的密码不一致'), findsOneWidget);
    expect(called, isFalse);
  });
}
