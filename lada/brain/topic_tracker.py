# lada/brain/topic_tracker.py — A1: о чём сейчас диалог.
#
# Раз в TOPIC_TRACKER_EVERY_N сообщений модель коротко говорит:
#   — о чём сейчас разговор (2-6 слов)
#   — тон диалога (лёгкий / серьёзный / флирт / спор / поддержка / грусть)
#   — «живой диалог идёт или провал в вежливость»
#
# Хранит в lada_state ключ focus_<user_id>.
# При смене фокуса — сдвигает старую тему в recent_topics (см. A2).

import asyncio
import json
import re
from datetime import datetime

import ollama

from config import (
    TOPIC_TRACKER_ENABLED,
    TOPIC_TRACKER_EVERY_N,
    MODEL,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.memory import get_history


_TOPIC_PROMPT = (
    "Ты — внутренний аналитик диалога. По последним сообщениям "
    "определи ОДНОЙ строкой:\n"
    "  — о чём СЕЙЧАС идёт разговор\n"
    "  — какой тон у диалога\n"
    "\n"
    "Верни СТРОГО JSON без markdown:\n"
    '{\n'
    '  "topic": "2-6 слов: о чём",\n'
    '  "tone": "лёгкий | серьёзный | флирт | спор | поддержка | '
    'грусть | интимный | нейтральный"\n'
    "}\n"
    "\n"
    "ПРАВИЛА:\n"
    "— topic — про смысл, не «переписываемся». "
    "«обсуждают поездку в горы», «он жалуется на работу», "
    "«флиртуют про внешность».\n"
    "— tone — одно слово из списка выше.\n"
    "— Если диалог непонятный — tone=нейтральный."
)


def _key(user_id: int) -> str:
    return f"focus_{user_id}"


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
    """Сколько сообщений прошло с момента последнего обновления фокуса."""
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
        "WHERE user_id = ? AND ts >= ?",
        (user_id, dt.isoformat(timespec="seconds")),
    ).fetchone()
    return int(rows["n"]) if rows else 0


async def _ask_model(user_id: int) -> dict | None:
    history = get_history(user_id, limit=10)
    if not history:
        return None

    lines = []
    for m in history[-10:]:
        role = "Он" if m.get("role") == "user" else "Я"
        content = (m.get("content") or "").strip()[:200]
        if content:
            lines.append(f"  {role}: {content}")

    if len(lines) < 3:
        return None

    messages = [
        {"role": "system", "content": _TOPIC_PROMPT},
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
        print(f"   ⚠️ topic_tracker LLM: {e}")
        return None

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if not isinstance(data, dict):
        return None

    topic = (data.get("topic") or "").strip()
    tone = (data.get("tone") or "").strip()

    if len(topic) < 3 or len(topic) > 120:
        return None
    if tone and len(tone) > 30:
        tone = tone[:30]

    return {"topic": topic, "tone": tone or "нейтральный"}


async def update_if_needed(user_id: int, force: bool = False) -> bool:
    if not TOPIC_TRACKER_ENABLED and not force:
        return False
    if not force and _count_since_update(user_id) < TOPIC_TRACKER_EVERY_N:
        return False

    data = await _ask_model(user_id)
    if not data:
        return False

    old = get(user_id)
    old_topic = (old or {}).get("topic")

    data["since"] = datetime.now().isoformat(timespec="seconds")
    _save(user_id, data)

    # Смена темы → сдвигаем старую в историю (см. recent_topics)
    if old_topic and old_topic != data["topic"]:
        try:
            from lada.brain.recent_topics import push_topic
            push_topic(user_id, old_topic, (old or {}).get("tone"))
        except Exception as e:
            print(f"   ⚠️ recent_topics push: {e}")

    print(f"   🎯 focus [{user_id}]: {data['topic']} / {data['tone']}")
    return True


def as_context_block(user_id: int) -> str:
    data = get(user_id)
    if not data:
        return ""
    topic = data.get("topic", "")
    tone = data.get("tone", "нейтральный")
    if not topic:
        return ""
    return (
        f"=== О ЧЁМ СЕЙЧАС РАЗГОВОР ===\n"
        f"Тема: {topic}\n"
        f"Тон диалога: {tone}\n"
    )