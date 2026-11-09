# 数据库

SQLite，WAL 模式，`foreign_keys=ON`。schema 在 `backend/infrastructure/schema.sql`，启动时 `CREATE TABLE IF NOT EXISTS` 幂等建表。

## 表

| 表 | 作用 | 关键约束 |
|---|---|---|
| `users` | 用户与套餐 | `plan IN ('free','pro')`，`tz` 决定额度按哪个时区跨天 |
| `devices` | 硬件设备 | `machine_code UNIQUE` |
| `device_bindings` | 设备与用户的归属，**有起止时间** | 部分唯一索引保证任何时刻只有一个活跃绑定 |
| `notes` | 一条记录（语音或文字） | `source IN ('voice','text')` |
| `jobs` | 处理任务与状态机 | `idempotency_key UNIQUE` |
| `summaries` | 摘要（一条 note 一份） | `note_id UNIQUE` |
| `todos` | 待办 | `status IN ('open','done')` |
| `reminders` | 提醒 | `state IN ('scheduled','fired','cancelled')` |
| `agent_runs` | 一次 Agent 运行 | 记录 provider/model/prompt 版本/tokens/耗时 |
| `agent_steps` | 运行里的每一步 | 前端 Trace 面板读这张表 |
| `pending_actions` | 待人工确认的不可逆操作 | `state IN ('pending','approved','rejected','expired','failed')` |
| `usage_ledger` | **只追加**的额度账本 | `UNIQUE(user_id, date_local, job_id, reason)` |
| `memories` | 记忆分片 | 与 FTS5 外部内容索引联动 |
| `memories_fts` | FTS5 trigram 索引 | `tokenize='trigram'`，由触发器同步 |

## 三张「不能出错」的表

### `usage_ledger`

```sql
CREATE TABLE usage_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    date_local TEXT NOT NULL,          -- 用户时区的自然日 YYYY-MM-DD
    delta      INTEGER NOT NULL,       -- +1 预占 / -1 退款
    reason     TEXT NOT NULL CHECK (reason IN ('reserve','refund')),
    job_id     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, date_local, job_id, reason)
);
```

- 当天用量 = `SELECT SUM(delta) WHERE user_id=? AND date_local=?`
- 预占是**条件插入**（判断和写入同一条语句），在 `BEGIN IMMEDIATE` 里执行
- 退款是 `INSERT OR IGNORE` 一条 `-1`，唯一键保证每个 job 每天最多退一次
- 账本永远不 UPDATE、不 DELETE

### `jobs`

状态机在 `domain/jobs.py` 里，是**白名单**：`_TRANSITIONS[src]` 不含 `dst` 就抛 `InvalidTransition`。`done` 是终态，不能回到 `queued`。

```
queued ──→ transcribing ──→ agent_running ──→ done
   │            │                 │
   └────────────┴─────────────────┴──→ queued (重试) / failed
```

### `pending_actions`

不可逆操作先落这里，`expires_at` 到期视为拒绝。只有 `POST /api/actions/{id}` 能把它推到 `approved` 并真正执行。

## FTS5 索引

外部内容模式，不复制正文：

```sql
CREATE VIRTUAL TABLE memories_fts
    USING fts5(text, content='memories', content_rowid='rowid', tokenize='trigram');
```

三个触发器（insert / delete / update）保持同步。查询用 `bm25(memories_fts)` 排序。

注意：trigram 至少需要 3 个字符才能匹配，1–2 字的查询在 `repos.memories_like_search` 里走 LIKE 兜底。

## 索引

```sql
ix_notes_user        (user_id, created_at DESC)
ix_jobs_state        (state, next_retry_at)      -- worker 取任务
ix_todos_user        (user_id, status, created_at DESC)
ix_reminders_due     (user_id, state, fire_at)   -- 到期扫描
ix_steps_run         (run_id, idx)
ix_pending_user      (user_id, state)
ix_ledger_user_day   (user_id, date_local)       -- 额度查询
ux_binding_active    (device_id) WHERE to_at IS NULL
```

## 迁移策略

MVP 用 `CREATE TABLE IF NOT EXISTS`，没有版本化迁移。要上线就得引入：加一张 `schema_version` 表，把每个变更写成独立的、可回滚的脚本。现在不做是因为没有存量数据，做了反而是空仪式。

## 备份

`data/nota.db` 单文件。WAL 模式下备份要用 `sqlite3 .backup` 或先 `PRAGMA wal_checkpoint(TRUNCATE)`，直接拷 `.db` 会丢最近的写。
