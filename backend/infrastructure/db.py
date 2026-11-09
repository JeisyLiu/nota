"""SQLite plumbing.

One short-lived connection per unit of work keeps things thread-safe without a
connection pool, and ``BEGIN IMMEDIATE`` gives us the write lock up front so the
conditional quota INSERT is genuinely atomic across concurrent threads and
processes.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        sql = SCHEMA_FILE.read_text(encoding="utf-8")
        with self.session() as conn:
            conn.executescript(sql)

    def reset(self) -> None:
        """Test helper: drop everything and recreate."""
        with self.session() as conn:
            rows = conn.execute(
                "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            conn.execute("PRAGMA foreign_keys=OFF")
            for row in rows:
                kind, name = row["type"], row["name"]
                if kind == "table":
                    conn.execute(f'DROP TABLE IF EXISTS "{name}"')
                elif kind == "view":
                    conn.execute(f'DROP VIEW IF EXISTS "{name}"')
                elif kind == "trigger":
                    conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Write transaction that takes the RESERVED lock immediately."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
