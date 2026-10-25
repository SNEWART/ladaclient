# lada/brain/promise_tracker.py — A3: обещания Лады.
#
# После каждого N-го ответа — короткий LLM-вызов:
# «Лада что-то пообещала в этом сообщении?»
# Если да — сохраняем в таблицу promises.
#
# В state_brief — блок «Ты обещала: …» для невыполненных, свежих.
# Раз в цикл — старые невыполненные обещания тихо закрываются.

import asyncio
import json
import re
from datetime import datetime, timedelta

import ollama

from config import (
    PROMISE_TRACKER_ENABLED,
    PROMISE_STALE_HOURS,
    MODEL,
)
from lada.core.db import get_conn, ensure_user, Transaction


_CHECK_PROMPT = (
    "Ты — аналитик обещаний. Проверь сообщение Лады: обещала ли она "
    "что-то конкретное СДЕЛАТЬ или РАССКАЗАТЬ?\n"
    "\n"
    "ОБЕЩАНИЕ — это когда она говорит «сейчас пришлю», «расскажу вечером», "
    "«покажу позже», «отвечу чуть позже», «попробую узнать».\n"
    "НЕ ОБЕЩАНИЕ — эмоции, мнения, вопросы, реакции.\n"
    "\n"
    "Верни СТРОГО JSON:\n"
    '{"promise": true|false, "text": "коротко что обещала", '
    '"deadline_hint": "когда (утро/вечер/сегодня/завтра/пусто)"}'
)


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


def _deadline_from_hint(hint: str) -> str | None:
    """Примитивный разбор подсказки в ISO."""
    h = (hint or "").strip().lower()
    if not h:
        return None
    now = datetime.now()
    if "сейчас" in h or "минут" in h:
        return (now + timedelta(minutes=30)).isoformat(timespec="seconds")
    if "вечер" in h:
        target = now.replace(hour=21, minute=0, second=0, microsecond=0)
        if target < now:
            target += timedelta(days=1)
        return target.isoformat(timespec="seconds")
    if "утро" in h:
        target = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if target < now:
            target += timedelta(days=1)
        return target.isoformat(timespec="seconds")
    if "завтра" in h:
        target = now + timedelta(days=1)
        return target.replace(hour=12, minute=0,
                              second=0, microsecond=0).isoformat(timespec="seconds")
    if "сегодня" in h:
        return (now + timedelta(hours=6)).isoformat(timespec="seconds")
    if "позже" in h or "потом" in h:
        return (now + timedelta(hours=3)).isoformat(timespec="seconds")
    return None


async def check_reply(user_id: int, bot_reply: str) -> bool:
    if not PROMISE_TRACKER_ENABLED:
        return False
    if not bot_reply or len(bot_reply.strip()) < 15:
        return False

    messages = [
        {"role": "system", "content": _CHECK_PROMPT},
        {"role": "user", "content": bot_reply[:600]},
    ]

    try:
        try:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                think=False, keep_alive="2m",
                options={"temperature": 0.0, "num_predict": 150},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="2m",
                options={"temperature": 0.0, "num_predict": 150},
            )
    except Exception as e:
        print(f"   ⚠️ promise_tracker LLM: {e}")
        return False

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if not isinstance(data, dict):
        return False

    if not data.get("promise"):
        return False

    text = (data.get("text") or "").strip()
    if len(text) < 5 or len(text) > 200:
        return False

    deadline = _deadline_from_hint(data.get("deadline_hint") or "")
    made_at = datetime.now().isoformat(timespec="seconds")

    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO promises (user_id, text, made_at, deadline) "
            "VALUES (?, ?, ?, ?)",
            (user_id, text, made_at, deadline),
        )

    print(f"   🤝 Обещание: {text[:100]}"
          + (f" (до {deadline[:16]})" if deadline else ""))
    return True


def _prune_stale():
    """Невыполненные обещания старше N часов — тихо закрываем."""
    cutoff = (datetime.now() - timedelta(hours=PROMISE_STALE_HOURS)
              ).isoformat(timespec="seconds")
    try:
        with Transaction() as conn:
            conn.execute(
                "UPDATE promises SET dismissed = 1 "
                "WHERE fulfilled = 0 AND dismissed = 0 "
                "AND made_at < ?",
                (cutoff,),
            )
    except Exception:
        pass


def get_active(user_id: int, limit: int = 3) -> list:
    _prune_stale()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, text, made_at, deadline FROM promises "
        "WHERE user_id = ? AND fulfilled = 0 AND dismissed = 0 "
        "ORDER BY made_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_fulfilled(user_id: int, promise_id: int):
    with Transaction() as conn:
        conn.execute(
            "UPDATE promises SET fulfilled = 1, "
            "fulfilled_at = ? WHERE id = ? AND user_id = ?",
            (datetime.now().isoformat(timespec="seconds"),
             promise_id, user_id),
        )


def as_context_block(user_id: int) -> str:
    items = get_active(user_id, limit=3)
    if not items:
        return ""
    lines = ["=== ТЫ ОБЕЩАЛА (не забудь) ==="]
    for it in items:
        text = it.get("text", "")
        dl = it.get("deadline")
        when = ""
        if dl:
            try:
                dt = datetime.fromisoformat(dl)
                delta = dt - datetime.now()
                h = int(delta.total_seconds() // 3600)
                if h < 0:
                    when = " (срок прошёл)"
                elif h < 2:
                    when = " (скоро)"
                else:
                    when = f" (через {h}ч)"
            except Exception:
                pass
        lines.append(f"  · {text}{when}")
    return "\n".join(lines)