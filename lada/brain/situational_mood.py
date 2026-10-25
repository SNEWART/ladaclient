# lada/brain/situational_mood.py — A4: настроение диалога (не глобальное mood).
#
# Это «как сейчас идёт разговор»: игривое, серьёзное, тёплое, флирт,
# холодное, поддержка. Отдельно от глобального mood Лады.
#
# Хранит в lada_state ключ sit_mood_<user_id>.

import asyncio
import json
import re
from datetime import datetime

import ollama

from config import (
    SITUATIONAL_MOOD_ENABLED,
    SITUATIONAL_MOOD_EVERY_N,
    MODEL,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.memory import get_history


_TONE_PROMPT = (
    "Определи, как сейчас идёт диалог (с точки зрения Лады).\n"
    "\n"
    "Верни СТРОГО JSON:\n"
    '{"vibe": "игривое | серьёзное | тёплое | флирт | холодное | '
    'поддержка | спор | задумчивое | нейтральное", '
    '"why": "коротко (3-6 слов) почему"}\n'
    "\n"
    "ПРАВИЛА:\n"
    "— Одно слово из списка.\n"
    "— Если неясно — нейтральное."
)


_VALID_VIBES = {
    "игривое", "серьёзное", "тёплое", "флирт", "холодное",
    "поддержка", "спор", "задумчивое", "нейтральное",
}


def _key(user_id: int) -> str:
    return f"sit_mood_{user_id}"


def _extract_json(text: str):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def get(user_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT value, updated FROM lada_state WHERE key = ?",
        (_key(user_id),),
    ).fetchone()
    if not row or not row["value"]:
        return None
    try:
        data = json.loads(row["value"])
        data["_updated"] = row["updated"]
        return data
    except Exception:
        return None


def _save(user_id: int, data: dict):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO lada_state (key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated = excluded.updated",
            (_key(user_id), json.dumps(data, ensure_ascii=False), ts),
        )


def _count_since_update(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT updated FROM lada_state WHERE key = ?",
        (_key(user_id),),
    ).fetchone()
    if not row or not row["updated"]:
        return 999
    try:
        dt = datetime.fromisoformat(row["updated"])
    except Exception:
        return 999
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE user_id = ? AND ts >= ?",
        (user_id, dt.isoformat(timespec="seconds")),
    ).fetchone()
    return int(rows["n"]) if rows else 0


async def _ask_model(user_id: int) -> dict | None:
    history = get_history(user_id, limit=8)
    if len(history) < 3:
        return None

    lines = []
    for m in history[-8:]:
        role = "Он" if m.get("role") == "user" else "Я"
        content = (m.get("content") or "").strip()[:180]
        if content:
            lines.append(f"  {role}: {content}")

    messages = [
        {"role": "system", "content": _TONE_PROMPT},
        {"role": "user", "content": "Диалог:\n" + "\n".join(lines)},
    ]

    try:
        try:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                think=False, keep_alive="2m",
                options={"temperature": 0.2, "num_predict": 80},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="2m",
                options={"temperature": 0.2, "num_predict": 80},
            )
    except Exception as e:
        print(f"   ⚠️ situational_mood LLM: {e}")
        return None

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if not isinstance(data, dict):
        return None

    vibe = (data.get("vibe") or "").strip().lower()
    why = (data.get("why") or "").strip()[:80]

    if vibe not in _VALID_VIBES:
        return None

    return {"vibe": vibe, "why": why}


async def update_if_needed(user_id: int, force: bool = False) -> bool:
    if not SITUATIONAL_MOOD_ENABLED and not force:
        return False
    if not force and _count_since_update(user_id) < SITUATIONAL_MOOD_EVERY_N:
        return False

    data = await _ask_model(user_id)
    if not data:
        return False

    old = get(user_id) or {}
    if old.get("vibe") != data["vibe"]:
        print(f"   🎭 Tone [{user_id}]: {data['vibe']} ({data['why']})")

    data["since"] = datetime.now().isoformat(timespec="seconds")
    _save(user_id, data)
    return True


def as_context_block(user_id: int) -> str:
    data = get(user_id)
    if not data:
        return ""
    vibe = data.get("vibe")
    if not vibe or vibe == "нейтральное":
        return ""
    why = data.get("why", "")
    suffix = f" ({why})" if why else ""
    return (
        f"=== ТОН ДИАЛОГА СЕЙЧАС ===\n"
        f"{vibe}{suffix}\n"
    )