# db.py — SQLite-обёртка для всего состояния Лады.

import json
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "lada.db"

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        conn = sqlite3.connect(
            str(DB_PATH),
            check_same_thread=False,
            isolation_level=None,
            timeout=15.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _local.depth = 0
    return _local.conn


def close_conn():
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
        _local.depth = 0


def _get_depth() -> int:
    if not hasattr(_local, "depth"):
        _local.depth = 0
    return _local.depth


def _set_depth(d: int):
    _local.depth = d


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id       INTEGER PRIMARY KEY,
        username      TEXT,
        first_name    TEXT,
        last_name     TEXT,
        phone         TEXT,
        city          TEXT,
        first_seen    TEXT,
        last_activity TEXT,
        updated       TEXT
    );

    CREATE TABLE IF NOT EXISTS user_state (
        user_id              INTEGER PRIMARY KEY,
        trust                REAL DEFAULT 0.0,
        intimacy             REAL DEFAULT 0.0,
        status               TEXT DEFAULT 'neutral',
        status_since         TEXT,
        status_note          TEXT,
        pending_proposal     TEXT,
        pending_since        TEXT,
        last_lover_proposal  TEXT,
        last_husband_proposal TEXT,
        last_rejection_which TEXT,
        last_rejection_at    TEXT,
        accepted             TEXT,
        accepted_at          TEXT,
        max_level_override   INTEGER,
        night_ok             INTEGER,
        night_pending        INTEGER,
        night_proposed_at    TEXT,
        night_rejected_at    TEXT,
        updated              TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS lada_state (
        key     TEXT PRIMARY KEY,
        value   TEXT,
        updated TEXT
    );

    CREATE TABLE IF NOT EXISTS messages (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id  INTEGER NOT NULL,
        role     TEXT NOT NULL,
        content  TEXT NOT NULL,
        ts       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id, id DESC);

    CREATE TABLE IF NOT EXISTS unread (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id  INTEGER NOT NULL,
        text     TEXT NOT NULL,
        ts       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_unread_user_id ON unread(user_id, id);

    CREATE TABLE IF NOT EXISTS unread_meta (
        user_id                 INTEGER PRIMARY KEY,
        last_unread_at          TEXT,
        last_return_attempt_at  TEXT
    );

    CREATE TABLE IF NOT EXISTS cooldowns (
        user_id    INTEGER PRIMARY KEY,
        touched_at TEXT
    );

    CREATE TABLE IF NOT EXISTS photo_history (
        user_id              INTEGER PRIMARY KEY,
        last_photo_msg_count INTEGER DEFAULT 0,
        total_photos         INTEGER DEFAULT 0,
        recent_prompts       TEXT,
        updated              TEXT
    );

    CREATE TABLE IF NOT EXISTS working_memory (
        user_id INTEGER PRIMARY KEY,
        content TEXT,
        updated TEXT
    );

    CREATE TABLE IF NOT EXISTS user_activity (
        user_id INTEGER PRIMARY KEY,
        last_activity TEXT
    );

    CREATE TABLE IF NOT EXISTS user_initiative (
        user_id INTEGER PRIMARY KEY,
        last_initiative TEXT
    );
    """)

    # Виртуальный "юзер" для глобальных таблиц
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, first_name, updated) "
        "VALUES (0, '__global__', datetime('now'))"
    )

    # Миграция: ночные колонки (для старых БД)
    _night_cols = [
        ("night_ok", "INTEGER"),
        ("night_pending", "INTEGER"),
        ("night_proposed_at", "TEXT"),
        ("night_rejected_at", "TEXT"),
    ]
    for col_name, col_type in _night_cols:
        try:
            conn.execute(
                f"ALTER TABLE user_state ADD COLUMN {col_name} {col_type}"
            )
        except Exception:
            pass


class Transaction:
    """Reentrant-транзакция."""

    def __enter__(self):
        self.conn = get_conn()
        depth = _get_depth()
        self.was_outermost = (depth == 0)
        if self.was_outermost:
            self.conn.execute("BEGIN IMMEDIATE")
        _set_depth(depth + 1)
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        depth = _get_depth()
        _set_depth(depth - 1)
        if self.was_outermost:
            if exc_type is None:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        return False


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _ensure_user(conn: sqlite3.Connection, user_id: int):
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, first_seen, updated) "
        "VALUES (?, ?, ?)",
        (user_id, _now(), _now()),
    )
    conn.execute(
        "INSERT OR IGNORE INTO user_state (user_id, updated) VALUES (?, ?)",
        (user_id, _now()),
    )


def ensure_user(user_id: int):
    with Transaction() as conn:
        _ensure_user(conn, user_id)