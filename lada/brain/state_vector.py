# lada/brain/state_vector.py — C2: вектор состояний.
#
# Помимо mood (одно слово) — 4 числовые шкалы 0..1:
#   energy     — энергия/активность
#   openness   — открытость к общению
#   warmth     — теплота
#   playfulness— игривость
#
# Обновляется раз в N ответов через LLM. Не заменяет mood,
# а дополняет: mood — общий тон, шкалы — нюансы.
#
# Хранится в lada_state, ключ state_vec_<uid>.

import asyncio
import json
import re
from datetime import datetime

import ollama

from config import (
    STATE_VECTOR_ENABLED,
    STATE_VECTOR_UPDATE_EVERY_N,
    MODEL,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.memory import get_history


_PROMPT = (
    "По последним сообщениям диалога оцени СВОЁ состояние по 4 шкалам "
    "от 0.0 до 1.0:\n"
    "\n"
    "  energy      — сколько у тебя энергии (0 — выжата, 1 — бодра)\n"
    "  openness    — насколько ты открыта этому человеку "
    "(0 — закрыта, 1 — полностью открыта)\n"
    "  warmth      — теплота к собеседнику "
    "(0 — холодна, 1 — очень тепло)\n"
    "  playfulness — игривость (0 — серьёзна, 1 — дурачишься)\n"
    "\n"
    "Верни СТРОГО JSON без markdown:\n"
    '{"energy": 0.0, "openness": 0.0, "warmth": 0.0, "playfulness": 0.0}'
)


def _key(user_id: int) -> str:
    return f"state_vec_{user_id}"


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


def _clamp01(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))


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
        "SELECT COUNT(*) AS n FROM messages "
        "WHERE user_id = ? AND role = 'assistant' AND ts >= ?",
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
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": "Диалог:\n" + "\n".join(lines)},
    ]

    try:
        try:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                think=False, keep_alive="2m",
                options={"temperature": 0.2, "num_predict": 100},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="2m",
                options={"temperature": 0.2, "num_predict": 100},
            )
    except Exception as e:
        print(f"   ⚠️ state_vector LLM: {e}")
        return None

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if not isinstance(data, dict):
        return None

    out = {
        "energy": _clamp01(data.get("energy")),
        "openness": _clamp01(data.get("openness")),
        "warmth": _clamp01(data.get("warmth")),
        "playfulness": _clamp01(data.get("playfulness")),
    }
    return out


async def update_if_needed(user_id: int, force: bool = False) -> bool:
    if not STATE_VECTOR_ENABLED and not force:
        return False
    if not force and _count_since_update(user_id) < STATE_VECTOR_UPDATE_EVERY_N:
        return False

    data = await _ask_model(user_id)
    if not data:
        return False

    old = get(user_id) or {}
    changed = {}
    for k in ("energy", "openness", "warmth", "playfulness"):
        old_v = old.get(k)
        new_v = data[k]
        if old_v is None or abs(float(old_v) - new_v) >= 0.2:
            changed[k] = round(new_v, 2)

    data["since"] = datetime.now().isoformat(timespec="seconds")
    _save(user_id, data)

    if changed:
        parts = ", ".join(f"{k}={v}" for k, v in changed.items())
        print(f"   📊 state_vector [{user_id}]: {parts}")

    return bool(changed)


def as_context_block(user_id: int) -> str:
    data = get(user_id)
    if not data:
        return ""

    # Описываем словами, а не числами — чтобы модель не тупила
    parts = []

    e = data.get("energy", 0.5)
    if e < 0.3:
        parts.append("мало энергии")
    elif e > 0.7:
        parts.append("бодрая")

    o = data.get("openness", 0.5)
    if o < 0.3:
        parts.append("закрыта")
    elif o > 0.7:
        parts.append("открыта")

    w = data.get("warmth", 0.5)
    if w < 0.3:
        parts.append("холодновата")
    elif w > 0.7:
        parts.append("теплая")

    p = data.get("playfulness", 0.5)
    if p < 0.3:
        parts.append("серьёзная")
    elif p > 0.7:
        parts.append("игривая")

    if not parts:
        return ""

    return (
        f"=== ТВОИ ШКАЛЫ ПРЯМО СЕЙЧАС ===\n"
        f"{', '.join(parts)}\n"
        f"(это нюансы тона, не приговор)\n"
    )