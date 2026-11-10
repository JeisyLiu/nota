import 'package:flutter_test/flutter_test.dart';
import 'package:nota_app/auth/session.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('AppUser', () {
    test('round-trips through JSON', () {
      const user = AppUser(
        userId: 'u_zhangwei',
        username: 'zhangwei',
        displayName: '张伟',
        plan: 'free',
        role: 'user',
      );
      final restored = AppUser.fromJson(user.toJson());
      expect(restored.userId, 'u_zhangwei');
      expect(restored.displayName, '张伟');
      expect(restored.isPro, isFalse);
      expect(restored.isAdmin, isFalse);
    });

    test('falls back to the username when there is no display name', () {
      final user = AppUser.fromJson({'user_id': 'u1', 'username': 'lina', 'plan': 'pro'});
      expect(user.displayName, 'lina');
      expect(user.isPro, isTrue);
    });

    test('initial is the first character, including for non-BMP names', () {
      const chinese = AppUser(
          userId: 'a', username: 'zhangwei', displayName: '张伟', plan: 'free',
          role: 'user');
      expect(chinese.initial, '张');

      const emoji = AppUser(
          userId: 'b', username: 'x', displayName: '😀 Team', plan: 'free',
          role: 'user');
      // Would be half a surrogate pair with substring(0, 1).
      expect(emoji.initial, '😀');
    });
  });

  group('SessionStore', () {
    test('saves, reads back and clears', () async {
      SharedPreferences.setMockInitialValues({});
      final store = await SessionStore.open();
      expect(store.readToken(), isNull);
      expect(store.readUser(), isNull);

      const user = AppUser(
          userId: 'u1', username: 'zhangwei', displayName: '张伟', plan: 'free',
          role: 'user');
      await store.save('tok-123', user);

      final reopened = await SessionStore.open();
      expect(reopened.readToken(), 'tok-123');
      expect(reopened.readUser()?.username, 'zhangwei');

      await reopened.clear();
      expect(reopened.readToken(), isNull);
      expect(reopened.readUser(), isNull);
    });

    test('a corrupt cached profile is treated as signed out', () async {
      SharedPreferences.setMockInitialValues({'nota.token': 'tok', 'nota.user': 'not json'});
      final store = await SessionStore.open();
      expect(store.readToken(), 'tok');
      expect(store.readUser(), isNull);
    });
  });
}
