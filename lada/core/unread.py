# unread.py — учёт непрочитанных сообщений (SQLite).
#
# FIX: as_context_block различает СВЕЖИЕ и СТАРЫЕ неотвеченные
# сообщения. Раньше блок всегда говорил «он ждёт ответа прямо сейчас»,
# даже если сообщению было 12 часов — модель галлюцинировала, что
# собеседник что-то говорил в текущем диалоге.

from datetime import datetime

from lada.core.db import get_conn, ensure_user, Transaction


MAX_STORED_MESSAGES = 30
UNREAD_STALE_HOURS = 4.0


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
    Блок для промпта про неотвеченные сообщения.
    Если они старые (> UNREAD_STALE_HOURS) — формулировка другая:
    «было давно», а не «он ждёт ответа сейчас».
    """
    pending = get_pending(user_id)
    if not pending:
        return ""

    n = len(pending)
    hours_old = hours_since_last_unread(user_id)
    is_stale = hours_old >= UNREAD_STALE_HOURS

    if is_stale:
        header = (
            f"=== ⏳ БЫЛО РАНЬШЕ: ТЫ НЕ ОТВЕТИЛА ЕМУ {n} РАЗ(А) ==="
        )
        intro = (
            f"Это старые сообщения ({int(hours_old)} ч назад). "
            "Он уже, скорее всего, переключился на другие дела. "
            "НЕ ПРЕТЕНДУЙ, что он ждёт прямо сейчас. "
            "НЕ ПИШИ «ты сказал», «ты скучаешь», «ты спрашивал» — "
            "этих слов в текущем диалоге НЕ БЫЛО."
        )
    else:
        header = f"=== ⏳ ЭТО ТЫ НЕ ОТВЕТИЛА ЕМУ {n} РАЗ(А) ==="
        intro = (
            "Это ТЫ молчала. Это ТЫ не отвечала. ОН писал — а ты "
            f"решила промолчать {n} раз(а). Он тебя НЕ игнорил."
        )

    lines = [header, "", intro, "", "Его сообщения:"]
    for m in pending[-10:]:
        ts = m.get("ts", "")[:16].replace("T", " ")
        lines.append(f"  [{ts}] {m['text'][:200]}")

    lines.append("")
    lines.append(
        "❗ НЕ ПРИПИСЫВАЙ ему слова, которых нет в разделе "
        "«СООБЩЕНИЕ» или в репликах «Он:» выше. Если тут написано "
        "«ты скучаешь» — это НЕ значит, что он так говорил. "
        "Если что-то непонятно — спроси прямо, не додумывай."
    )
    return "\n".join(lines)