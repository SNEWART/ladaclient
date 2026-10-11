# unread.py — учёт непрочитанных сообщений (SQLite).
#
# FIX: as_context_block теперь явно объясняет модели, что ЭТО ОНА
# молчала, а не он. Раньше формулировка «он написал, ты не ответила»
# читалась моделью зеркально — она думала, что игнорят её.

from datetime import datetime

from lada.core.db import get_conn, ensure_user, Transaction


MAX_STORED_MESSAGES = 30


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def append(user_id: int, text: str):
    if not text or not text.strip():
        return
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO unread (user_id, text, ts) VALUES (?, ?, ?)",
            (user_id, text.strip()[:500], ts),
        )
        conn.execute(
            "DELETE FROM unread WHERE user_id = ? AND id NOT IN ("
            "  SELECT id FROM unread WHERE user_id = ? "
            "  ORDER BY id DESC LIMIT ?"
            ")",
            (user_id, user_id, MAX_STORED_MESSAGES),
        )
        conn.execute(
            "INSERT INTO unread_meta (user_id, last_unread_at) "
            "VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "last_unread_at = excluded.last_unread_at",
            (user_id, ts),
        )


def mark_read(user_id: int):
    with Transaction() as conn:
        conn.execute("DELETE FROM unread WHERE user_id = ?", (user_id,))
        conn.execute(
            "UPDATE unread_meta SET last_unread_at = NULL, "
            "last_return_attempt_at = NULL WHERE user_id = ?",
            (user_id,),
        )


def get_pending(user_id: int) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT text, ts FROM unread WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()
    return [{"text": r["text"], "ts": r["ts"]} for r in rows]


def count(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM unread WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row["n"] if row else 0


def list_users_with_pending() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM unread"
    ).fetchall()
    return [r["user_id"] for r in rows]


def hours_since_last_unread(user_id: int) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT last_unread_at FROM unread_meta WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["last_unread_at"]:
        return 999.0
    try:
        dt = datetime.fromisoformat(row["last_unread_at"])
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def hours_since_last_return_attempt(user_id: int) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT last_return_attempt_at FROM unread_meta WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["last_return_attempt_at"]:
        return 999.0
    try:
        dt = datetime.fromisoformat(row["last_return_attempt_at"])
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def touch_return_attempt(user_id: int):
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO unread_meta (user_id, last_return_attempt_at) "
            "VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "last_return_attempt_at = excluded.last_return_attempt_at",
            (user_id, ts),
        )


def as_context_block(user_id: int) -> str:
    """
    Блок для промпта: «он написал тебе N раз, а ТЫ не ответила».
    Формулировка специально про «ТЫ молчала», чтобы модель не
    перевернула ситуацию и не решила, что игнорят её.
    """
    pending = get_pending(user_id)
    if not pending:
        return ""

    n = len(pending)
    lines = [
        f"=== ⏳ ЭТО ТЫ НЕ ОТВЕТИЛА ЕМУ {n} РАЗ(А) ===",
        "",
        "🔴 ВАЖНО ПОНЯТЬ ПРАВИЛЬНО:",
        "Это ТЫ молчала. Это ТЫ не отвечала. ОН писал — а ты "
        f"решила промолчать {n} раз(а) подряд. Он тебя НЕ игнорил. "
        "Он тебе писал. Ты его не услышала — своим ответом.",
        "",
        "Его сообщения, на которые ты НЕ ответила:",
    ]
    for m in pending[-10:]:
        ts = m.get("ts", "")[:16].replace("T", " ")
        lines.append(f"  [{ts}] {m['text'][:200]}")
    lines.append("")
    lines.append(
        "❗ НЕ ПУТАЙ: не он тебя игнорирует — а ты его. "
        "Если ты сейчас ответишь — это твой выбор вернуться к нему. "
        "Если снова промолчишь — это тоже твой выбор. "
        "Но тогда не жалуйся, что тебя игнорят: это ты молчишь."
    )
    return "\n".join(lines)