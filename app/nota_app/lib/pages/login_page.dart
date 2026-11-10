import 'package:flutter/material.dart';

import '../api/nota_client.dart';

/// 登录 / 注册二合一。
///
/// 只负责收集凭据并调用后端；令牌与账号的保存在 [AppGate] 里，页面本身不碰
/// 存储，这样这个页面可以被 widget test 直接驱动。
class LoginPage extends StatefulWidget {
  const LoginPage({
    super.key,
    required this.client,
    required this.onLoggedIn,
    this.notice,
    this.baseUrl = '',
  });

  final NotaClient client;
  final ValueChanged<AuthResult> onLoggedIn;

  /// 例如「登录已过期，请重新登录」，由 AppGate 传入。
  final String? notice;
  final String baseUrl;

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  final _displayName = TextEditingController();

  bool _registering = false;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _username.dispose();
    _password.dispose();
    _confirm.dispose();
    _displayName.dispose();
    super.dispose();
  }

  String? _validate() {
    final username = _username.text.trim();
    if (username.length < 2) return '用户名至少 2 个字符';
    if (_password.text.length < 6) return '密码至少 6 位';
    if (_registering && _password.text != _confirm.text) return '两次输入的密码不一致';
    return null;
  }

  String _message(Object error) {
    if (error is NotaApiError) {
      if (error.status == 401) return '用户名或密码不正确';
      if (error.status == 409) return '用户名已被占用，换一个试试';
      return error.message;
    }
    return '无法连接后端${widget.baseUrl.isEmpty ? '' : '（${widget.baseUrl}）'}';
  }

  Future<void> _submit() async {
    final problem = _validate();
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = _registering
          ? await widget.client.register(
              username: _username.text.trim(),
              password: _password.text,
              displayName: _displayName.text,
            )
          : await widget.client.login(_username.text.trim(), _password.text);
      if (!mounted) return;
      widget.onLoggedIn(result);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = _message(error);
      });
    }
  }

  void _useDemoAccount() {
    setState(() {
      _registering = false;
      _username.text = 'zhangwei';
      _password.text = '123456';
      _error = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 380),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
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
                      const Text('Nota',
                          style: TextStyle(fontSize: 22, fontWeight: FontWeight.w600)),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(
                    _registering ? '创建一个账号，开始记录' : '登录后可以看到自己的记录与额度',
                    style: const TextStyle(fontSize: 13, color: Color(0xFF8B93A7)),
                  ),
                  if (widget.notice != null) ...[
                    const SizedBox(height: 14),
                    _Banner(text: widget.notice!),
                  ],
                  const SizedBox(height: 20),
                  SegmentedButton<bool>(
                    segments: const [
                      ButtonSegment(value: false, label: Text('登录')),
                      ButtonSegment(value: true, label: Text('注册')),
                    ],
                    selected: {_registering},
                    showSelectedIcon: false,
                    onSelectionChanged: _busy
                        ? null
                        : (selection) => setState(() {
                              _registering = selection.first;
                              _error = null;
                            }),
                  ),
                  const SizedBox(height: 18),
                  TextField(
                    controller: _username,
                    enabled: !_busy,
                    autofillHints: const [AutofillHints.username],
                    decoration: const InputDecoration(labelText: '用户名', isDense: true),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _password,
                    enabled: !_busy,
                    obscureText: true,
                    autofillHints: const [AutofillHints.password],
                    decoration: const InputDecoration(labelText: '密码', isDense: true),
                    onSubmitted: (_) => _submit(),
                  ),
                  if (_registering) ...[
                    const SizedBox(height: 12),
                    TextField(
                      controller: _confirm,
                      enabled: !_busy,
                      obscureText: true,
                      decoration: const InputDecoration(labelText: '确认密码', isDense: true),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _displayName,
                      enabled: !_busy,
                      decoration:
                          const InputDecoration(labelText: '昵称（可选）', isDense: true),
                    ),
                  ],
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Text(_error!,
                        style: const TextStyle(fontSize: 12, color: Color(0xFFFCA5A5))),
                  ],
                  const SizedBox(height: 20),
                  FilledButton(
                    onPressed: _busy ? null : _submit,
                    child: _busy
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : Text(_registering ? '注册并登录' : '登录'),
                  ),
                  const SizedBox(height: 16),
                  TextButton(
                    onPressed: _busy ? null : _useDemoAccount,
                    child: const Text('使用演示账号 zhangwei / 123456',
                        style: TextStyle(fontSize: 12)),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0xFF2A2416),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFF4A3B12)),
      ),
      child: Text(text,
          style: const TextStyle(fontSize: 12, color: Color(0xFFFBBF24), height: 1.4)),
    );
  }
}
