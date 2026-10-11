# contacts.py — сводка знакомств Лады: кто, как давно, краткое описание.

from datetime import datetime

from lada.core.db import get_conn
from lada.core import users_names


def _days_since(iso_ts: str) -> int:
    if not iso_ts:
        return 0
    try:
        dt = datetime.fromisoformat(iso_ts)
        return max(0, int((datetime.now() - dt).total_seconds() / 86400))
    except Exception:
        return 0


def _status_ru(status: str) -> str:
    mapping = {
        "neutral": "обычный знакомый",
        "acquaintance": "знакомый",
        "lover": "любовник",
        "husband": "муж",
    }
    return mapping.get(status, "знакомый")


def get_recent_observations(user_id: int, limit: int = 3) -> list:
    from lada.core.lancedb_memory import lance_mem as faiss_mem
    items = [
        m for m in faiss_mem.metadata
        if m.get("category") == "observation" and m.get("user_id") == user_id
    ]
    items = items[-limit:]
    items.reverse()
    return [m.get("text", "") for m in items if m.get("text")]


def get_contacts(min_messages: int = 1) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            u.user_id,
            u.first_name,
            u.username,
            u.first_seen,
            u.last_activity,
            COALESCE(s.status, 'neutral') AS status,
            (SELECT COUNT(*) FROM messages m WHERE m.user_id = u.user_id) AS msg_count
        FROM users u
        LEFT JOIN user_state s ON s.user_id = u.user_id
        WHERE u.user_id > 0
    """).fetchall()

    out = []
    for r in rows:
        uid = r["user_id"]
        msg_count = r["msg_count"] or 0
        if msg_count < min_messages:
            continue

        name = users_names.display_name(uid)
        days = _days_since(r["first_seen"])
        status = r["status"] or "neutral"

        out.append({
            "user_id": uid,
            "name": name,
            "status": status,
            "status_ru": _status_ru(status),
            "days_known": days,
            "msg_count": msg_count,
            "last_seen": r["last_activity"] or "",
            "snippets": get_recent_observations(uid, limit=2),
        })

    out.sort(key=lambda x: x["last_seen"] or "", reverse=True)
    return out


def build_contacts_block(current_user_id: int) -> str:
    contacts = get_contacts(min_messages=1)
    if not contacts:
        return ""

    total = len(contacts)
    lines = [
        "=== ЛЮДИ, КОТОРЫХ ТЫ ЗНАЕШЬ ===",
        f"Всего в твоей памяти: {total}.",
        "",
        "Это люди, с которыми ты уже общалась. Ты их помнишь. "
        "Можешь подтвердить, что знакома — но НЕ раскрываешь "
        "детали их личной жизни другим.",
        "",
    ]

    for c in contacts[:10]:
        mark = ""
        if c["user_id"] == current_user_id:
            mark = "  ← сейчас говоришь с ним"

        desc = f"- {c['name']} (ID {c['user_id']})"
        desc += f" — {c['status_ru']}, знакома {c['days_known']} дн."
        if c["snippets"]:
            snippet = c["snippets"][0][:100]
            desc += f" Помнишь: {snippet}"
        desc += mark
        lines.append(desc)

    if total > 10:
        lines.append(f"... и ещё {total - 10}.")

    return "\n".join(lines)