import 'package:flutter_test/flutter_test.dart';
import 'package:nota_app/models/models.dart';

void main() {
  group('Quota', () {
    test('exhausted only at the limit', () {
      expect(const Quota(used: 9, limit: 10).exhausted, isFalse);
      expect(const Quota(used: 10, limit: 10).exhausted, isTrue);
    });

    test('pro plan is never exhausted', () {
      expect(const Quota(used: 999, limit: null, plan: 'pro').exhausted, isFalse);
    });
  });

  group('NoteJob', () {
    test('parses the backend payload', () {
      final job = NoteJob.fromJson({
        'job_id': 'job_1',
        'note_id': 'note_1',
        'state': 'done',
        'summary': '已整理',
        'title': '发报价单',
        'todos': [
          {'id': 'todo_1', 'title': '发报价单', 'due_at': '2026-11-10T15:00:00+08:00',
           'status': 'open'},
        ],
        'reminders': [
          {'id': 'rem_1', 'title': '发报价单', 'fire_at': '2026-11-10T15:00:00+08:00',
           'state': 'scheduled'},
        ],
        'attempts': 0,
        'error': null,
      });
      expect(job.finished, isTrue);
      expect(job.todos.single.done, isFalse);
      expect(job.reminders.single.fireAt, startsWith('2026-11-10T15:00'));
      expect(job.stateLabel, '已完成');
    });

    test('a failed job is terminal', () {
      final job = NoteJob.fromJson({
        'job_id': 'job_2', 'note_id': 'note_2', 'state': 'failed',
        'attempts': 3, 'error': 'no audio',
      });
      expect(job.finished, isTrue);
      expect(job.error, 'no audio');
    });
  });

  test('quota exceeded carries the numbers the UI shows', () {
    const error = QuotaExceeded(used: 10, limit: 10, resetAt: '2026-11-10T00:00:00+08:00');
    expect(error.toString(), contains('10/10'));
  });
}
