-- Nota MVP schema. SQLite, WAL, foreign keys on.
-- The quota ledger is append-only; agent trace is append-only too.

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT,
    plan        TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro')),
    tz          TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id           TEXT PRIMARY KEY,
    machine_code TEXT NOT NULL UNIQUE,
    model        TEXT,
    fw_version   TEXT,
    created_at   TEXT NOT NULL
);

-- A device can be sold or handed over; ownership is a time-bounded binding.
CREATE TABLE IF NOT EXISTS device_bindings (
    id        TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id),
    user_id   TEXT NOT NULL REFERENCES users(id),
    from_at   TEXT NOT NULL,
    to_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_binding_active
    ON device_bindings(device_id) WHERE to_at IS NULL;

CREATE TABLE IF NOT EXISTS notes (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id),
    device_id  TEXT REFERENCES devices(id),
    source     TEXT NOT NULL CHECK (source IN ('voice', 'text')),
    transcript TEXT NOT NULL DEFAULT '',
    audio_path TEXT,
    lang       TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notes_user ON notes(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    note_id         TEXT NOT NULL REFERENCES notes(id),
    user_id         TEXT NOT NULL REFERENCES users(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    state           TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_retry_at   TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_state ON jobs(state, next_retry_at);

CREATE TABLE IF NOT EXISTS summaries (
    id         TEXT PRIMARY KEY,
    note_id    TEXT NOT NULL UNIQUE REFERENCES notes(id),
    title      TEXT,
    summary_md TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id           TEXT PRIMARY KEY,
    note_id      TEXT REFERENCES notes(id),
    user_id      TEXT NOT NULL REFERENCES users(id),
    title        TEXT NOT NULL,
    due_at       TEXT,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_todos_user ON todos(user_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS reminders (
    id         TEXT PRIMARY KEY,
    note_id    TEXT REFERENCES notes(id),
    todo_id    TEXT REFERENCES todos(id),
    user_id    TEXT NOT NULL REFERENCES users(id),
    title      TEXT NOT NULL,
    fire_at    TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'scheduled' CHECK (state IN ('scheduled', 'fired', 'cancelled')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reminders_due ON reminders(user_id, state, fire_at);

CREATE TABLE IF NOT EXISTS agent_runs (
    id             TEXT PRIMARY KEY,
    note_id        TEXT,
    user_id        TEXT NOT NULL REFERENCES users(id),
    provider       TEXT,
    model          TEXT,
    prompt_version TEXT,
    status         TEXT,
    tokens_in      INTEGER NOT NULL DEFAULT 0,
    tokens_out     INTEGER NOT NULL DEFAULT 0,
    latency_ms     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES agent_runs(id),
    idx        INTEGER NOT NULL,
    type       TEXT NOT NULL,
    name       TEXT,
    args_json  TEXT,
    result_json TEXT,
    ms         INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_steps_run ON agent_steps(run_id, idx);

-- Irreversible tool calls wait here for a human decision. Timeout == rejected.
CREATE TABLE IF NOT EXISTS pending_actions (
    id          TEXT PRIMARY KEY,
    run_id      TEXT,
    user_id     TEXT NOT NULL REFERENCES users(id),
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    risk        TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'approved', 'rejected', 'expired', 'failed')),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    resolved_at TEXT,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_pending_user ON pending_actions(user_id, state);

-- Append-only quota ledger. delta = +1 on reserve, -1 on refund.
CREATE TABLE IF NOT EXISTS usage_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL REFERENCES users(id),
    date_local TEXT NOT NULL,
    delta      INTEGER NOT NULL,
    reason     TEXT NOT NULL CHECK (reason IN ('reserve', 'refund')),
    job_id     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, date_local, job_id, reason)
);
CREATE INDEX IF NOT EXISTS ix_ledger_user_day ON usage_ledger(user_id, date_local);

CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id),
    note_id    TEXT REFERENCES notes(id),
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_memories_user ON memories(user_id, created_at DESC);

-- External-content FTS index: no duplicated text, triggers keep it in sync.
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(text, content='memories', content_rowid='rowid', tokenize='trigram');

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO memories_fts(rowid, text) VALUES (new.rowid, new.text);
END;
