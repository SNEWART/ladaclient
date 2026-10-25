# lada/brain/companion_model.py — B3+B4: модель собеседника и стиль с ним.
#
# Раз в N ответов — по последним сообщениям определяем:
#   — что сейчас с собеседником (устал / весел / флиртует / грустит / ...)
#   — как с ним общаться (шутить / серьёзно / тепло / держать дистанцию)
#   — короткая заметка «что происходит»
#
# Хранится в lada_state под ключом companion_<uid>.

import asyncio
import json
import re
from datetime import datetime

import ollama

from config import (
    COMPANION_MODEL_ENABLED,
    COMPANION_MODEL_EVERY_N,
    MODEL,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.memory import get_history


_PROMPT = (
    "Ты — внутренний аналитик диалога. По последним сообщениям "
    "собеседника и Лады определи:\n"
    "  — состояние собеседника СЕЙЧАС\n"
    "  — стиль общения с ним (как с ним лучше говорить)\n"
    "  — короткую заметку «что происходит»\n"
    "\n"
    "Верни СТРОГО JSON:\n"
    "{\n"
    '  "state": "устал | весел | флиртует | грустит | задумчив | '
    'зол | занят | спокоен | нейтрален",\n'
    '  "style": "шутить | серьёзно | тепло | держать дистанцию | '
    'флиртовать | поддерживать",\n'
    '  "note": "1 короткая фраза (5-12 слов)"\n'
    "}\n"
    "\n"
    "ПРАВИЛА:\n"
    "— Одно значение из списка для state и style.\n"
    "— note — про то, что реально видно из сообщений: "
    "«он пришёл уставший после работы», «он подкалывает про кофе».\n"
    "— Если неясно — state=нейтрален, style=тепло."
)


_VALID_STATES = {
    "устал", "весел", "флиртует", "грустит", "задумчив",
    "зол", "занят", "спокоен", "нейтрален",
}
_VALID_STYLES = {
    "шутить", "серьёзно", "тепло", "держать дистанцию",
    "флиртовать", "поддерживать",
}


def _key(user_id: int) -> str:
    return f"companion_{user_id}"


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
        "SELECT COUNT(*) AS n FROM messages "
        "WHERE user_id = ? AND role = 'assistant' AND ts >= ?",
        (user_id, dt.isoformat(timespec="seconds")),
    ).fetchone()
    return int(rows["n"]) if rows else 0


async def _ask_model(user_id: int) -> dict | None:
    history = get_history(user_id, limit=10)
    if len(history) < 4:
        return None

    lines = []
    for m in history[-10:]:
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
                options={"temperature": 0.2, "num_predict": 120},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="2m",
                options={"temperature": 0.2, "num_predict": 120},
            )
    except Exception as e:
        print(f"   ⚠️ companion_model LLM: {e}")
        return None

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if not isinstance(data, dict):
        return None

    state = (data.get("state") or "").strip().lower()
    style = (data.get("style") or "").strip().lower()
    note = (data.get("note") or "").strip()[:120]

    if state not in _VALID_STATES:
        state = "нейтрален"
    if style not in _VALID_STYLES:
        style = "тепло"

    return {"state": state, "style": style, "note": note}


async def update_if_needed(user_id: int, force: bool = False) -> bool:
    if not COMPANION_MODEL_ENABLED and not force:
        return False
    if not force and _count_since_update(user_id) < COMPANION_MODEL_EVERY_N:
        return False

    data = await _ask_model(user_id)
    if not data:
        return False

    old = get(user_id) or {}
    data["since"] = datetime.now().isoformat(timespec="seconds")
    _save(user_id, data)

    if old.get("state") != data["state"] or old.get("style") != data["style"]:
        print(f"   👤 companion [{user_id}]: "
              f"{data['state']} / {data['style']} — {data['note'][:60]}")

    return True


def as_context_block(user_id: int) -> str:
    data = get(user_id)
    if not data:
        return ""
    state = data.get("state", "нейтрален")
    style = data.get("style", "тепло")
    note = data.get("note", "")

    if state == "нейтрален" and style == "тепло" and not note:
        return ""

    lines = ["=== ЧТО С НИМ СЕЙЧАС ==="]
    lines.append(f"Состояние: {state}")
    lines.append(f"Стиль общения: {style}")
    if note:
        lines.append(f"Заметка: {note}")
    return "\n".join(lines)