import 'package:flutter/material.dart';

import '../api/nota_client.dart';
import '../auth/session.dart';
import '../models/models.dart';

/// 个人信息：账号资料、今日额度、使用统计、改密码、退出登录。
class ProfilePage extends StatefulWidget {
  const ProfilePage({
    super.key,
    required this.client,
    required this.user,
    required this.onLogout,
  });

  final NotaClient client;
  final AppUser user;
  final Future<void> Function() onLogout;

  @override
  State<ProfilePage> createState() => _ProfilePageState();
}

class _ProfilePageState extends State<ProfilePage> {
  AppUser? _profile;
  Quota? _quota;
  Map<String, dynamic>? _stats;
  bool _loading = true;
  String? _error;
  bool _loggingOut = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait<Object>([
        widget.client.profile(),
        widget.client.quota(),
        widget.client.myStats(),
      ]);
      if (!mounted) return;
      setState(() {
        _profile = results[0] as AppUser;
        _quota = results[1] as Quota;
        _stats = results[2] as Map<String, dynamic>;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = error is NotaApiError ? error.message : '加载失败，请检查后端是否在运行';
      });
    }
  }

  Future<void> _changePassword() async {
    final current = TextEditingController();
    final next = TextEditingController();
    final changed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('修改密码', style: TextStyle(fontSize: 16)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: current,
              obscureText: true,
              decoration: const InputDecoration(labelText: '当前密码', isDense: true),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: next,
              obscureText: true,
              decoration: const InputDecoration(labelText: '新密码（至少 6 位）', isDense: true),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('确定')),
        ],
      ),
    );
    if (changed != true || !mounted) return;
    if (next.text.length < 6) {
      _snack('新密码至少 6 位');
      return;
    }
    try {
      await widget.client.changePassword(current.text, next.text);
      _snack('密码已修改，其它设备已被退出登录');
    } catch (error) {
      _snack(error is NotaApiError ? error.message : '修改失败');
    }
  }

  Future<void> _confirmLogout() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('退出登录', style: TextStyle(fontSize: 16)),
        content: const Text('退出后需要重新输入用户名和密码。', style: TextStyle(fontSize: 13)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('退出')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _loggingOut = true);
    await widget.onLogout();
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(text), behavior: SnackBarBehavior.floating));
  }

  @override
  Widget build(BuildContext context) {
    final profile = _profile ?? widget.user;
    final quota = _quota;
    final stats = _stats?['stats'] as Map<String, dynamic>?;

    return Scaffold(
      appBar: AppBar(
        title: const Text('个人信息', style: TextStyle(fontSize: 16)),
        actions: [
          IconButton(onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
              children: [
                _header(profile),
                if (_error != null) ...[
                  const SizedBox(height: 12),
                  Text(_error!, style: const TextStyle(fontSize: 12, color: Color(0xFFFCA5A5))),
                ],
                const SizedBox(height: 18),
                if (quota != null) _quotaCard(quota),
                if (stats != null) ...[
                  const SizedBox(height: 12),
                  _statsCard(stats),
                ],
                const SizedBox(height: 20),
                _sectionTitle('账号资料'),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                    child: Column(
                      children: [
                        _row('用户名', profile.username),
                        _row('显示名', profile.displayName),
                        _row('套餐', profile.isPro ? 'Pro（不限额度）' : 'Free（每日 10 次）'),
                        _row('角色', profile.isAdmin ? '管理员' : '普通用户'),
                        _row('账号 ID', profile.userId),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 20),
                OutlinedButton.icon(
                  onPressed: _changePassword,
                  icon: const Icon(Icons.lock_outline, size: 18),
                  label: const Text('修改密码'),
                ),
                const SizedBox(height: 10),
                TextButton.icon(
                  onPressed: _loggingOut ? null : _confirmLogout,
                  icon: const Icon(Icons.logout, size: 18),
                  label: Text(_loggingOut ? '正在退出…' : '退出登录'),
                  style: TextButton.styleFrom(foregroundColor: const Color(0xFFFCA5A5)),
                ),
              ],
            ),
    );
  }

  Widget _header(AppUser user) {
    return Row(
      children: [
        CircleAvatar(
          radius: 26,
          backgroundColor: const Color(0xFF1D212A),
          child: Text(user.initial,
              style: const TextStyle(fontSize: 22, color: Color(0xFF6EE7B7))),
        ),
        const SizedBox(width: 14),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(user.displayName,
                  style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
              const SizedBox(height: 4),
              Text('@${user.username}',
                  style: const TextStyle(fontSize: 12, color: Color(0xFF8B93A7))),
            ],
          ),
        ),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(
            color: user.isPro ? const Color(0xFF13302A) : const Color(0xFF23303F),
            borderRadius: BorderRadius.circular(99),
          ),
          child: Text(
            user.isPro ? 'Pro' : 'Free',
            style: TextStyle(
              fontSize: 11,
              color: user.isPro ? const Color(0xFF6EE7B7) : const Color(0xFF38BDF8),
            ),
          ),
        ),
      ],
    );
  }

  Widget _quotaCard(Quota quota) {
    final exhausted = quota.exhausted;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Text('今日额度', style: TextStyle(fontSize: 13)),
                const Spacer(),
                Text(
                  quota.limit == null ? '${quota.used} / 不限' : '${quota.used} / ${quota.limit}',
                  style: const TextStyle(fontSize: 13, color: Color(0xFF8B93A7)),
                ),
              ],
            ),
            const SizedBox(height: 10),
            ClipRRect(
              borderRadius: BorderRadius.circular(99),
              child: LinearProgressIndicator(
                value: quota.ratio.clamp(0.0, 1.0),
                minHeight: 8,
                backgroundColor: const Color(0xFF22262F),
                valueColor: AlwaysStoppedAnimation<Color>(
                  exhausted ? const Color(0xFFFBBF24) : const Color(0xFF6EE7B7),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              exhausted
                  ? '今日额度已用完，本地时间零点重置'
                  : quota.remaining == null
                      ? 'Pro 套餐不限额'
                      : '还剩 ${quota.remaining} 次',
              style: TextStyle(
                fontSize: 11,
                color: exhausted ? const Color(0xFFFBBF24) : const Color(0xFF8B93A7),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _statsCard(Map<String, dynamic> stats) {
    final notes = stats['notes'] as Map<String, dynamic>? ?? const {};
    final todos = stats['todos'] as Map<String, dynamic>? ?? const {};
    final jobs = stats['jobs'] as Map<String, dynamic>? ?? const {};
    final rate = jobs['success_rate'];
    return Row(
      children: [
        Expanded(child: _metric('记录', '${notes['total'] ?? 0}')),
        const SizedBox(width: 10),
        Expanded(child: _metric('待办未完成', '${todos['open'] ?? 0}')),
        const SizedBox(width: 10),
        Expanded(
          child: _metric('成功率',
              rate == null ? '—' : '${(100 * (rate as num).toDouble()).round()}%'),
        ),
      ],
    );
  }

  Widget _metric(String label, String value) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 14),
        child: Column(
          children: [
            Text(value, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            Text(label, style: const TextStyle(fontSize: 11, color: Color(0xFF8B93A7))),
          ],
        ),
      ),
    );
  }

  Widget _sectionTitle(String text) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(text,
            style: const TextStyle(fontSize: 13, color: Color(0xFF8B93A7), letterSpacing: 1)),
      );

  Widget _row(String label, String value) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            SizedBox(
              width: 80,
              child: Text(label,
                  style: const TextStyle(fontSize: 12, color: Color(0xFF8B93A7))),
            ),
            Expanded(
              child: Text(value, style: const TextStyle(fontSize: 13)),
            ),
          ],
        ),
      );
}
