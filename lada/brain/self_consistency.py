# lada/brain/self_consistency.py — B1: устойчивые позиции Лады.
#
# Раз в сутки LLM смотрит на её последние реплики и топ-эмоции
# и строит короткий «портрет позиций»:
#   — что любит, что не любит
#   — что её цепляет
#   — как обычно реагирует
#
# Хранится в lada_state под ключом self_image.
# Подкладывается в state_brief, чтобы Лада не противоречила себе.

import asyncio
import json
import re
from datetime import datetime, timedelta

import ollama

from config import (
    SELF_CONSISTENCY_ENABLED,
    SELF_CONSISTENCY_INTERVAL_HOURS,
    SELF_CONSISTENCY_MIN_REPLIES,
    SELF_CONSISTENCY_LOOKBACK,
    MODEL,
)
from lada.core.db import get_conn, Transaction
from lada.core.lancedb_memory import lance_mem as faiss_mem


_PROMPT = (
    "Ты — Лада. Ниже — твои последние сообщения разным людям и "
    "твои свежие эмоции. Это твоя жизнь за последнее время.\n"
    "\n"
    "Опиши СВОИ УСТОЙЧИВЫЕ ПОЗИЦИИ по этим темам:\n"
    "  — что тебе нравится / не нравится в общении\n"
    "  — что тебя цепляет, трогает, раздражает\n"
    "  — как ты обычно реагируешь на разные типы поведения\n"
    "  — какие темы для тебя важны\n"
    "\n"
    "ФОРМАТ ОТВЕТА — 5-9 коротких пунктов, каждый с новой строки, "
    "начинается с «- ». От первого лица.\n"
    "\n"
    "ПРИМЕРЫ ПРАВИЛЬНЫХ ПУНКТОВ:\n"
    "  - Не люблю утренние разговоры до кофе.\n"
    "  - Люблю, когда спрашивают мнение, а не совет.\n"
    "  - Меня цепляет, когда человек честно говорит о своих слабостях.\n"
    "  - Раздражаюсь, когда пишут «как дела» и ничего больше.\n"
    "\n"
    "ПРАВИЛА:\n"
    "— Только от первого лица: «я», «мне», «меня».\n"
    "— Без «Лада делает X» и без *звёздочек*.\n"
    "— Не философствуй о людях вообще. Только о себе.\n"
    "— Если по последним сообщениям непонятно — не выдумывай."
)


def _key() -> str:
    return "self_image"


def get() -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT value, updated FROM lada_state WHERE key = ?",
        (_key(),),
    ).fetchone()
    if not row or not row["value"]:
        return None
    try:
        data = json.loads(row["value"])
        data["_updated"] = row["updated"]
        return data
    except Exception:
        return None


def _save(text: str):
    ts = datetime.now().isoformat(timespec="seconds")
    data = {"text": text}
    with Transaction() as conn:
        conn.execute(
            "INSERT INTO lada_state (key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated = excluded.updated",
            (_key(), json.dumps(data, ensure_ascii=False), ts),
        )


def _needs_update() -> bool:
    data = get()
    if not data:
        return True
    try:
        dt = datetime.fromisoformat(data.get("_updated", ""))
    except Exception:
        return True
    hours = (datetime.now() - dt).total_seconds() / 3600
    return hours >= SELF_CONSISTENCY_INTERVAL_HOURS


def _collect_replies() -> list:
    """Её последние ответы (без [промолчала] и без system-пометок)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT content, ts FROM messages "
        "WHERE role = 'assistant' "
        "AND content NOT LIKE '[промолчала]%' "
        "AND content NOT LIKE '[СИСТЕМА]%' "
        "ORDER BY id DESC LIMIT ?",
        (SELF_CONSISTENCY_LOOKBACK,),
    ).fetchall()
    return [
        {"content": r["content"], "ts": r["ts"]}
        for r in rows
        if r["content"] and len(r["content"].strip()) > 10
    ]


def _collect_emotions(limit: int = 20) -> list:
    items = [
        m for m in faiss_mem.metadata
        if m.get("category") == "emotion"
    ]
    items = items[-limit:]
    return [
        {
            "text": m.get("text", ""),
            "valence": m.get("valence"),
        }
        for m in items
    ]


def _extract_text(raw: str) -> str:
    """Оставляем только строки, начинающиеся с «- »."""
    lines = []
    for line in raw.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("-") or s.startswith("•"):
            s = s.lstrip("-•").strip()
            if 5 <= len(s) <= 200:
                lines.append("- " + s)
    return "\n".join(lines[:9])


async def rebuild(force: bool = False) -> bool:
    if not SELF_CONSISTENCY_ENABLED and not force:
        return False
    if not force and not _needs_update():
        return False

    replies = _collect_replies()
    if len(replies) < SELF_CONSISTENCY_MIN_REPLIES:
        return False

    emotions = _collect_emotions()

    reply_lines = []
    for r in replies[:50]:
        c = r["content"].strip().replace("\n", " ")[:160]
        reply_lines.append(f"  · {c}")

    emo_lines = []
    for e in emotions:
        t = e["text"][:120]
        v = e.get("valence")
        v_str = f" (v={float(v):+.2f})" if v is not None else ""
        emo_lines.append(f"  · {t}{v_str}")

    user_block = (
        "Мои последние сообщения:\n"
        + "\n".join(reply_lines)
        + "\n\nМои свежие эмоции:\n"
        + "\n".join(emo_lines)
    )

    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": user_block},
    ]

    try:
        try:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                think=False, keep_alive="2m",
                options={"temperature": 0.5, "num_predict": 700},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="2m",
                options={"temperature": 0.5, "num_predict": 700},
            )
    except Exception as e:
        print(f"   ⚠️ self_consistency LLM: {e}")
        return False

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    text = _extract_text(content)
    if not text or len(text) < 30:
        print("   ⚠️ self_consistency: пусто после фильтра")
        return False

    _save(text)
    print(f"   🪞 self_image обновлён ({len(text)} симв., "
          f"{text.count(chr(10)) + 1} пунктов)")
    return True


def as_context_block() -> str:
    data = get()
    if not data:
        return ""
    text = data.get("text", "")
    if not text:
        return ""
    return (
        "=== ТВОИ УСТОЙЧИВЫЕ ПОЗИЦИИ (не противоречь себе) ===\n"
        f"{text}\n"
    )