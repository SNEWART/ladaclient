# memory.py — история диалога (SQLite)

from pathlib import Path
from datetime import datetime

from lada.core.db import get_conn, ensure_user, Transaction

DATA_DIR = Path("data")
USERS_DIR = DATA_DIR / "users"
DATA_DIR.mkdir(exist_ok=True)


def _user_file(user_id: int) -> Path:
    return USERS_DIR / f"{user_id}.json"


def get_history(user_id: int, limit: int = None) -> list:
    conn = get_conn()
    if limit:
        rows = conn.execute(
            "SELECT role, content, ts FROM messages "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        rows = list(reversed(rows))
    else:
        rows = conn.execute(
            "SELECT role, content, ts FROM messages "
            "WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()

    return [
        {
            "role": r["role"],
            "content": r["content"],
            "timestamp": r["ts"],
        }
        for r in rows
    ]


def add_message(user_id: int, role: str, content: str, max_history: int = 20):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO messages (user_id, role, content, ts) "
            "VALUES (?, ?, ?, ?)",
            (user_id, role, content, ts),
        )
        conn.execute(
            "DELETE FROM messages WHERE user_id = ? AND id NOT IN ("
            "  SELECT id FROM messages WHERE user_id = ? "
            "  ORDER BY id DESC LIMIT ?"
            ")",
            (user_id, user_id, max_history),
        )


def add_silence_marker(user_id: int, max_history: int = 20):
    """
    Помечает, что Лада решила промолчать.
    Сохраняется как ASSISTANT-реплика — модель видит, что она
    участвовала в диалоге (пусть и молчанием), не путает поток
    его сообщений со своими мыслями.
    """
    add_message(user_id, "assistant", "[промолчала]",
                max_history=max_history)


def clear_history(user_id: int):
    with Transaction() as conn:
        conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))


def list_users() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT user_id FROM messages").fetchall()
    return [r["user_id"] for r in rows]