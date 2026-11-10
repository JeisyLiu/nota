import 'dart:async';

import 'package:flutter/material.dart';

import 'api/nota_client.dart';
import 'auth/session.dart';
import 'pages/home_page.dart';
import 'pages/login_page.dart';

/// 应用外壳：决定显示登录页还是主页，并持有唯一的 [NotaClient]。
///
/// 令牌存在 [SessionStore]；页面不碰存储。任何接口返回 401 都会通过
/// [NotaClient.onUnauthorized] 回到这里，统一清凭据并回登录页。
class AppGate extends StatefulWidget {
  const AppGate({super.key, required this.baseUrl});

  final String baseUrl;

  @override
  State<AppGate> createState() => _AppGateState();
}

class _AppGateState extends State<AppGate> {
  late final NotaClient _client;
  SessionStore? _store;
  AppUser? _user;
  bool _booting = true;
  String? _notice;

  @override
  void initState() {
    super.initState();
    _client = NotaClient(baseUrl: widget.baseUrl)..onUnauthorized = _onUnauthorized;
    unawaited(_bootstrap());
  }

  @override
  void dispose() {
    // The client lives as long as the app: logging out and back in reuses it.
    _client.dispose();
    super.dispose();
  }

  Future<void> _bootstrap() async {
    try {
      // A platform storage plugin that never answers must not leave the app on a
      // spinner forever, so the read is bounded.
      final store = await SessionStore.open().timeout(const Duration(seconds: 5));
      final token = store.readToken();
      final user = store.readUser();
      if (!mounted) return;
      _store = store;
      if (token != null) _client.authToken = token;
      setState(() {
        // A token without a cached profile still counts as signed in; the first
        // request will either succeed or bounce us back to the login page.
        _user = token == null ? null : (user ?? _placeholder(token));
        _booting = false;
      });
    } catch (error, stack) {
      // Local storage is a convenience, not a requirement: fall through to the
      // login page rather than trapping the user behind a spinner.
      debugPrint('session restore failed: $error\n$stack');
      if (!mounted) return;
      setState(() {
        _booting = false;
        _user = null;
        _notice = '本地登录状态读取失败，请重新登录';
      });
    }
  }

  /// Very short-lived stand-in while `/api/me/profile` loads on the home screen.
  AppUser _placeholder(String token) => const AppUser(
        userId: '',
        username: '…',
        displayName: '…',
        plan: 'free',
        role: 'user',
      );

  void _onUnauthorized() {
    if (!mounted) return;
    unawaited(_clear('登录已过期，请重新登录'));
  }

  Future<void> _clear(String? notice) async {
    await _store?.clear();
    _client.authToken = null;
    if (!mounted) return;
    setState(() {
      _user = null;
      _notice = notice;
    });
  }

  Future<void> _loggedIn(AuthResult result) async {
    _client.authToken = result.token;
    await _store?.save(result.token, result.user);
    if (!mounted) return;
    setState(() {
      _user = result.user;
      _notice = null;
    });
  }

  Future<void> _logout() async {
    await _client.logout();
    await _clear(null);
  }

  @override
  Widget build(BuildContext context) {
    if (_booting) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    final user = _user;
    if (user == null) {
      return LoginPage(
        client: _client,
        baseUrl: widget.baseUrl,
        notice: _notice,
        onLoggedIn: (result) => unawaited(_loggedIn(result)),
      );
    }
    return HomePage(client: _client, user: user, onLogout: _logout);
  }
}
