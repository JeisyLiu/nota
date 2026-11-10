import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// The signed-in account, as the app needs it.
///
/// Mirrors `account_payload` on the server; no password or token lives here.
class AppUser {
  const AppUser({
    required this.userId,
    required this.username,
    required this.displayName,
    required this.plan,
    required this.role,
  });

  final String userId;
  final String username;
  final String displayName;
  final String plan;
  final String role;

  bool get isAdmin => role == 'admin';
  bool get isPro => plan == 'pro';

  /// First character, for the little avatar circle.
  ///
  /// Uses runes rather than `substring(0, 1)` so a name starting with an emoji or
  /// any non-BMP character does not get sliced in half.
  String get initial {
    final source = displayName.trim().isNotEmpty ? displayName.trim() : username;
    if (source.isEmpty) return '?';
    return String.fromCharCodes(source.runes.take(1));
  }

  Map<String, dynamic> toJson() => {
        'user_id': userId,
        'username': username,
        'display_name': displayName,
        'plan': plan,
        'role': role,
      };

  factory AppUser.fromJson(Map<String, dynamic> json) => AppUser(
        userId: (json['user_id'] ?? '') as String,
        username: (json['username'] ?? '') as String,
        displayName: (json['display_name'] ?? json['username'] ?? '') as String,
        plan: (json['plan'] ?? 'free') as String,
        role: (json['role'] ?? 'user') as String,
      );
}

/// Token + cached profile, persisted between launches.
///
/// `shared_preferences` is not encrypted. That is acceptable for this demo and is
/// called out in the README; a production build would use the platform keystore
/// (Keychain / Keystore) for the token.
class SessionStore {
  SessionStore(this._prefs);

  static const _tokenKey = 'nota.token';
  static const _userKey = 'nota.user';

  final SharedPreferences _prefs;

  static Future<SessionStore> open() async =>
      SessionStore(await SharedPreferences.getInstance());

  String? readToken() {
    final token = _prefs.getString(_tokenKey);
    return (token == null || token.isEmpty) ? null : token;
  }

  AppUser? readUser() {
    final raw = _prefs.getString(_userKey);
    if (raw == null || raw.isEmpty) return null;
    try {
      return AppUser.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } catch (_) {
      // Corrupt cache: treat as signed out rather than crashing at startup.
      return null;
    }
  }

  Future<void> save(String token, AppUser user) async {
    await _prefs.setString(_tokenKey, token);
    await _prefs.setString(_userKey, jsonEncode(user.toJson()));
  }

  Future<void> clear() async {
    await _prefs.remove(_tokenKey);
    await _prefs.remove(_userKey);
  }
}
