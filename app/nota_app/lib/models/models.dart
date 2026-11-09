/// 与后端 JSON 一一对应的数据模型。
///
/// 刻意保持薄：不做业务逻辑，只做解析和展示。
library;

class Quota {
  final int used;
  final int? limit;
  final int? remaining;
  final String plan;

  const Quota({required this.used, this.limit, this.remaining, this.plan = 'free'});

  factory Quota.fromJson(Map<String, dynamic> json) => Quota(
        used: (json['used'] as num?)?.toInt() ?? 0,
        limit: (json['limit'] as num?)?.toInt(),
        remaining: (json['remaining'] as num?)?.toInt(),
        plan: json['plan'] as String? ?? 'free',
      );

  bool get exhausted => limit != null && used >= limit!;

  double get ratio => limit == null || limit == 0 ? 0.1 : used / limit!;
}

class Todo {
  final String id;
  final String title;
  final String? dueAt;
  final String status;

  const Todo({required this.id, required this.title, this.dueAt, this.status = 'open'});

  factory Todo.fromJson(Map<String, dynamic> json) => Todo(
        id: json['id'] as String,
        title: json['title'] as String,
        dueAt: json['due_at'] as String?,
        status: json['status'] as String? ?? 'open',
      );

  bool get done => status == 'done';
}

class Reminder {
  final String id;
  final String title;
  final String fireAt;
  final String state;

  const Reminder({required this.id, required this.title, required this.fireAt,
      this.state = 'scheduled'});

  factory Reminder.fromJson(Map<String, dynamic> json) => Reminder(
        id: json['id'] as String,
        title: json['title'] as String,
        fireAt: json['fire_at'] as String,
        state: json['state'] as String? ?? 'scheduled',
      );
}

class NoteJob {
  final String jobId;
  final String noteId;
  final String state;
  final String? summary;
  final String? title;
  final List<Todo> todos;
  final List<Reminder> reminders;
  final int attempts;
  final String? error;

  const NoteJob({
    required this.jobId,
    required this.noteId,
    required this.state,
    this.summary,
    this.title,
    this.todos = const [],
    this.reminders = const [],
    this.attempts = 0,
    this.error,
  });

  factory NoteJob.fromJson(Map<String, dynamic> json) => NoteJob(
        jobId: json['job_id'] as String,
        noteId: json['note_id'] as String,
        state: json['state'] as String,
        summary: json['summary'] as String?,
        title: json['title'] as String?,
        todos: (json['todos'] as List<dynamic>? ?? [])
            .map((e) => Todo.fromJson(e as Map<String, dynamic>))
            .toList(),
        reminders: (json['reminders'] as List<dynamic>? ?? [])
            .map((e) => Reminder.fromJson(e as Map<String, dynamic>))
            .toList(),
        attempts: (json['attempts'] as num?)?.toInt() ?? 0,
        error: json['error'] as String?,
      );

  bool get finished => state == 'done' || state == 'failed';

  String get stateLabel => switch (state) {
        'queued' => '排队中',
        'transcribing' => '转写中',
        'agent_running' => '整理中',
        'done' => '已完成',
        'failed' => '失败',
        _ => state,
      };
}

class NoteSummary {
  final String noteId;
  final String createdAt;
  final String source;
  final String transcript;
  final String? state;
  final String? title;

  const NoteSummary({
    required this.noteId,
    required this.createdAt,
    required this.source,
    required this.transcript,
    this.state,
    this.title,
  });

  factory NoteSummary.fromJson(Map<String, dynamic> json) => NoteSummary(
        noteId: json['note_id'] as String,
        createdAt: json['created_at'] as String,
        source: json['source'] as String,
        transcript: json['transcript'] as String? ?? '',
        state: json['state'] as String?,
        title: json['title'] as String?,
      );
}

class AgentStep {
  final int idx;
  final String type;
  final String? name;
  final dynamic args;
  final dynamic result;
  final int ms;

  const AgentStep({
    required this.idx,
    required this.type,
    this.name,
    this.args,
    this.result,
    this.ms = 0,
  });

  factory AgentStep.fromJson(Map<String, dynamic> json) => AgentStep(
        idx: (json['idx'] as num).toInt(),
        type: json['type'] as String,
        name: json['name'] as String?,
        args: json['args'],
        result: json['result'],
        ms: (json['ms'] as num?)?.toInt() ?? 0,
      );
}

class PendingAction {
  final String id;
  final String tool;
  final Map<String, dynamic> args;
  final String risk;

  const PendingAction({required this.id, required this.tool, required this.args,
      required this.risk});

  factory PendingAction.fromJson(Map<String, dynamic> json) => PendingAction(
        id: json['id'] as String,
        tool: json['tool'] as String,
        args: Map<String, dynamic>.from(json['args'] as Map),
        risk: json['risk'] as String? ?? 'irreversible',
      );
}

class AskResult {
  final String answer;
  final List<Map<String, dynamic>> citations;
  final int retrieved;
  final bool grounded;

  const AskResult({
    required this.answer,
    this.citations = const [],
    this.retrieved = 0,
    this.grounded = true,
  });

  factory AskResult.fromJson(Map<String, dynamic> json) => AskResult(
        answer: json['answer'] as String? ?? '',
        citations: (json['citations'] as List<dynamic>? ?? [])
            .map((e) => Map<String, dynamic>.from(e as Map))
            .toList(),
        retrieved: (json['retrieved'] as num?)?.toInt() ?? 0,
        grounded: json['grounded'] as bool? ?? true,
      );
}

class QuotaExceeded implements Exception {
  final int used;
  final int limit;
  final String resetAt;
  const QuotaExceeded({required this.used, required this.limit, required this.resetAt});

  @override
  String toString() => '今日免费额度已用完（$used/$limit），本地时间零点重置';
}
