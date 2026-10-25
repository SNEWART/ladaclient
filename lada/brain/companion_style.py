# lada/brain/companion_style.py — стиль собеседника.
#
# Лада подмечает, КАК он говорит: слова, манера, шутки, темы.
# Отдельно от companion_model (тот про состояние и тон диалога).
#
# Здесь именно профиль стиля:
#   — какие слова использует (лексикон)
#   — как шутит (сарказм, самоирония, абсурд, без шуток)
#   — как строит фразы (коротко/развёрнуто, мат/формально)
#   — что цепляет / трогает / бесит
#   — какие темы избегает
#
# Используется:
#   1. В state_brief — Лада знает, как с ним говорить.
#   2. В future-модулях — можно подстраивать ответы под лексику.

import asyncio
import json
import re
from datetime import datetime

import ollama

from config import (
    COMPANION_STYLE_ENABLED,
    COMPANION_STYLE_EVERY_N,
    COMPANION_STYLE_LOOKBACK,
    MODEL,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.memory import get_history


_PROMPT = (
    "Ты — аналитик стиля общения. По репликам собеседника определи, "
    "КАК он говорит. Не что он сказал, а как.\n"
    "\n"
    "Верни СТРОГО JSON:\n"
    "{\n"
    '  "lexicon": ["его характерные слова или обороты", "..."],\n'
    '  "humor": "сарказм | самоирония | абсурд | добрый | чёрный | '
    'без шуток | редкий",\n'
    '  "length": "коротко | средне | развёрнуто",\n'
    '  "formality": "формально | обычный | мат | сленг | смешанный",\n'
    '  "triggers_positive": ["что его радует", "..."],\n'
    '  "triggers_negative": ["что его бесит / трогает плохо", "..."],\n'
    '  "avoid_topics": ["темы, которые он обходит", "..."],\n'
    '  "summary": "1-2 предложения: как с ним говорить"\n'
    "}\n"
    "\n"
    "ПРАВИЛА:\n"
    "— lexicon: 3-7 конкретных слов/фраз, которые ОН использует "
    "часто («короче», «типа», «блин», «на самом деле»). Только "
    "реальные примеры из его реплик.\n"
    "— humor: одно слово из списка.\n"
    "— triggers_positive/negative: по 1-4 пункта. Только если реально "
    "видно из сообщений. Пустой массив — если нечего сказать.\n"
    "— avoid_topics: темы, которые он обходит или закрывает коротко.\n"
    "— summary: конкретно, не абстрактно. «С ним можно шутить, но не "
    "про работу — раздражается».\n"
    "— Только русский язык."
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


def get(user_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT style_json, updated FROM companion_styles "
        "WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["style_json"]:
        return None
    try:
        data = json.loads(row["style_json"])
        data["_updated"] = row["updated"]
        return data
    except Exception:
        return None


def _save(user_id: int, data: dict):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO companion_styles (user_id, style_json, updated) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "style_json = excluded.style_json, "
            "updated = excluded.updated",
            (user_id, json.dumps(data, ensure_ascii=False), ts),
        )


def _count_since_update(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT updated FROM companion_styles WHERE user_id = ?",
        (user_id,),
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


def _collect_his_messages(user_id: int, limit: int = 30) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT content, ts FROM messages "
        "WHERE user_id = ? AND role = 'user' "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [
        {"content": r["content"], "ts": r["ts"]}
        for r in rows
        if r["content"] and len(r["content"].strip()) > 3
    ]


async def update_if_needed(user_id: int, force: bool = False) -> bool:
    if not COMPANION_STYLE_ENABLED and not force:
        return False
    if not force and _count_since_update(user_id) < COMPANION_STYLE_EVERY_N:
        return False

    his = _collect_his_messages(user_id, limit=COMPANION_STYLE_LOOKBACK)
    if len(his) < 10:
        # мало данных — ждём ещё
        return False

    # Свежие сверху, но при сборке промпта — от старых к новым
    his_sorted = list(reversed(his))[:25]

    lines = []
    for m in his_sorted:
        content = (m["content"] or "").strip().replace("\n", " ")[:200]
        if content:
            lines.append(f"  · {content}")

    user_block = (
        "Реплики собеседника (от старых к новым):\n"
        + "\n".join(lines)
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
                options={"temperature": 0.3, "num_predict": 600},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="2m",
                options={"temperature": 0.3, "num_predict": 600},
            )
    except Exception as e:
        print(f"   ⚠️ companion_style LLM: {e}")
        return False

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if not isinstance(data, dict):
        return False

    # Нормализация
    lexicon = data.get("lexicon") or []
    if isinstance(lexicon, list):
        lexicon = [str(x).strip()[:40] for x in lexicon if str(x).strip()][:7]
    else:
        lexicon = []

    humor = (data.get("humor") or "").strip().lower()[:30]
    length = (data.get("length") or "").strip().lower()[:20]
    formality = (data.get("formality") or "").strip().lower()[:20]

    def _list_of_str(v, n=4):
        if not isinstance(v, list):
            return []
        out = []
        for x in v:
            s = str(x).strip()[:80]
            if s and len(s) > 3:
                out.append(s)
            if len(out) >= n:
                break
        return out

    out = {
        "lexicon": lexicon,
        "humor": humor,
        "length": length,
        "formality": formality,
        "triggers_positive": _list_of_str(data.get("triggers_positive"), 4),
        "triggers_negative": _list_of_str(data.get("triggers_negative"), 4),
        "avoid_topics": _list_of_str(data.get("avoid_topics"), 4),
        "summary": (data.get("summary") or "").strip()[:250],
    }

    old = get(user_id) or {}
    _save(user_id, out)

    if old.get("summary") != out["summary"]:
        print(f"   👤 Стиль [{user_id}]: {out['summary'][:100]}")
        if out.get("lexicon"):
            print(f"      лексика: {', '.join(out['lexicon'][:5])}")

    return True


# ─────────────── промпт-блок ───────────────

def as_context_block(user_id: int) -> str:
    data = get(user_id)
    if not data:
        return ""

    summary = data.get("summary", "")
    lexicon = data.get("lexicon") or []
    humor = data.get("humor", "")
    positive = data.get("triggers_positive") or []
    negative = data.get("triggers_negative") or []
    avoid = data.get("avoid_topics") or []

    # Если совсем нет ничего — не выводим
    if not any([summary, lexicon, humor, positive, negative, avoid]):
        return ""

    lines = ["=== ЕГО СТИЛЬ ОБЩЕНИЯ ==="]
    if summary:
        lines.append(summary)

    if lexicon:
        lines.append(f"Его слова/обороты: {', '.join(lexicon[:6])}")
        lines.append(
            "(Можешь использовать их в своей речи — но не через слово, "
            "это выглядит карикатурно.)"
        )

    if humor and humor not in ("", "без шуток"):
        lines.append(f"Юмор: {humor}")

    if positive:
        lines.append(f"Радует: {', '.join(positive[:3])}")

    if negative:
        lines.append(f"Задевает: {', '.join(negative[:3])}")

    if avoid:
        lines.append(f"Обходит темы: {', '.join(avoid[:3])}")

    return "\n".join(lines)