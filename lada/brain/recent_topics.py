# lada/brain/recent_topics.py — A2: история тем за последние часы.
#
# Хранит в lada_state ключ recent_topics_<user_id>:
# список [{topic, tone, ts}, ...] максимум RECENT_TOPICS_MAX.
# Старые записи (> RECENT_TOPICS_TTL_MIN) выбрасываются.
#
# Служит для state_brief: «за последние часы вы говорили о…».

import json
from datetime import datetime, timedelta

from config import RECENT_TOPICS_MAX, RECENT_TOPICS_TTL_MIN
from lada.core.db import get_conn, ensure_user, Transaction


def _key(user_id: int) -> str:
    return f"recent_topics_{user_id}"


def _load(user_id: int) -> list:
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM lada_state WHERE key = ?",
        (_key(user_id),),
    ).fetchone()
    if not row or not row["value"]:
        return []
    try:
        return json.loads(row["value"]) or []
    except Exception:
        return []


def _save(user_id: int, items: list):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO lada_state (key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated = excluded.updated",
            (_key(user_id), json.dumps(items, ensure_ascii=False), ts),
        )


def _prune(items: list) -> list:
    cutoff = datetime.now() - timedelta(minutes=RECENT_TOPICS_TTL_MIN)
    out = []
    for it in items:
        try:
            dt = datetime.fromisoformat(it.get("ts", ""))
        except Exception:
            continue
        if dt >= cutoff:
            out.append(it)
    return out[-RECENT_TOPICS_MAX:]


def push_topic(user_id: int, topic: str, tone: str | None = None):
    if not topic:
        return
    items = _prune(_load(user_id))
    # Не дублировать последнюю тему
    if items and items[-1].get("topic") == topic:
        return
    items.append({
        "topic": topic,
        "tone": tone or "",
        "ts": datetime.now().isoformat(timespec="seconds"),
    })
    _save(user_id, items[-RECENT_TOPICS_MAX:])


def get_recent(user_id: int) -> list:
    return _prune(_load(user_id))


def as_context_block(user_id: int) -> str:
    items = get_recent(user_id)
    if not items:
        return ""
    # Игнорируем самый последний — это текущий фокус
    history = items[:-1] if len(items) > 1 else []
    if not history:
        return ""

    lines = ["=== О ЧЁМ ВЫ ГОВОРИЛИ РАНЕЕ (за последние часы) ==="]
    for it in history[-4:]:
        ts = it.get("ts", "")[:16].replace("T", " ")
        tone = it.get("tone") or ""
        suffix = f" ({tone})" if tone else ""
        lines.append(f"  · [{ts}] {it['topic']}{suffix}")
    return "\n".join(lines)