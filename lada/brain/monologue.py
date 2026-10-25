# monologue.py — внутренний монолог Лады
#
# FIX: дедупликация. Раньше модель писала одну и ту же мысль
# ("люди либо хотят объяснить, либо дать место") по 20 раз в сутки.

import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path
import ollama

from config import (
    MODEL, MONOLOGUE_ENABLED,
    MONOLOGUE_INTERVAL_MINUTES, MONOLOGUE_FIRST_DELAY_MINUTES,
    MONOLOGUE_INJECT_RECENT_HOURS, MONOLOGUE_INJECT_MAX,
)
from lada.core.persona import get_persona
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core.memory import list_users, get_history

MONOLOGUE_LOG = Path("data") / "monologue.log"
MONOLOGUE_LOG.parent.mkdir(exist_ok=True)

DUPLICATE_THRESHOLD = 0.85

MONOLOGUE_PROMPT = (
    "Ты сейчас наедине с собой. Это твой внутренний монолог — "
    "никто его не прочитает, кроме тебя самой.\n\n"
    "Подумай о чём-нибудь, что тебя сейчас занимает: о недавнем "
    "разговоре, о пользователе, о чём-то, что тебя зацепило, "
    "о своём настроении.\n\n"
    "ПРАВИЛА:\n"
    "— Пиши ТОЛЬКО от первого лица: «я», «мне», «у меня».\n"
    "— НЕ пиши «Лада думает», «она чувствует» — ты не наблюдаешь "
    "себя со стороны, ты говоришь изнутри.\n"
    "— Никаких *звёздочек* и (скобок).\n"
    "— Никаких вопросов. Ты не обращаешься ни к кому.\n"
    "— 1–3 предложения. Коротко.\n"
    "— Не философствуй «про людей вообще». Думай о конкретном."
)


def _tokens(s: str) -> set:
    return set(re.findall(r'\w{4,}', s.lower()))


def _is_duplicate(text: str) -> float:
    """Возвращает max overlap с последними монологами (0..1)."""
    recent = [
        m for m in faiss_mem.metadata
        if m.get("category") == "monologue"
    ][-10:]
    new_tokens = _tokens(text)
    if not new_tokens:
        return 0.0
    max_overlap = 0.0
    for m in recent:
        old_tokens = _tokens(m.get("text", ""))
        if not old_tokens:
            continue
        overlap = len(new_tokens & old_tokens) / max(
            len(new_tokens), len(old_tokens)
        )
        max_overlap = max(max_overlap, overlap)
    return max_overlap


def _build_context() -> str:
    parts = []

    # недавние диалоги
    users = list_users()
    if users:
        parts.append("Что было недавно в разговорах:")
        for uid in users[:3]:
            history = get_history(uid, limit=6)
            if not history:
                continue
            snippets = [
                f"  {m['role']}: {m['content'][:120]}"
                for m in history[-4:]
            ]
            parts.append(f"  (с ID {uid})\n" + "\n".join(snippets))

    # недавняя память (без монологов)
    recent = [
        m for m in faiss_mem.metadata
        if m.get("category") != "monologue"
    ][-8:]
    if recent:
        parts.append("\nЧто лежит в памяти:")
        for m in recent:
            parts.append(f"  [{m.get('category', '?')}] {m['text'][:150]}")

    return "\n".join(parts)


async def think_once() -> str:
    """Одна итерация внутреннего монолога."""
    if not MONOLOGUE_ENABLED:
        return ""

    context = _build_context()
    user_content = (
        f"Вот что сейчас в твоей голове:\n\n{context}"
        if context
        else "Просто посиди в тишине и подумай."
    )

    messages = [
        {"role": "system", "content": get_persona() + "\n\n" + MONOLOGUE_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        resp = await asyncio.to_thread(
            ollama.chat,
            model=MODEL,
            messages=messages,
            options={"temperature": 0.9, "num_predict": 1500},
        )
    except Exception as e:
        print(f"⚠️ Ошибка монолога: {e}")
        return ""

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()

    if thinking:
        print(f"   🧠 Размышления ({len(thinking)} симв.)")

    text = content

    if not text or len(text) < 10:
        print("   ⚠️ Модель не дошла до финальной мысли, пропускаю")
        return ""

    # FIX: дедупликация — не пишем, если похоже на последние 10 монологов
    try:
        overlap = _is_duplicate(text)
        if overlap > DUPLICATE_THRESHOLD:
            print(f"   🔁 Похоже на прошлую мысль (overlap={overlap:.2f}), "
                  f"пропускаю")
            return ""
    except Exception as e:
        print(f"   ⚠️ dedup-проверка упала: {e}")

    faiss_mem.add(text, category="monologue", source="monologue")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(MONOLOGUE_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n[{timestamp}] {text}\n")

    print(f"   💭 Мысль: {text[:80]}")
    return text


def get_recent_thoughts() -> list[dict]:
    """Свежие мысли за последние N часов (для подмешивания в контекст)."""
    cutoff = (
        datetime.now() - timedelta(hours=MONOLOGUE_INJECT_RECENT_HOURS)
    ).isoformat(timespec="seconds")
    return [
        m for m in faiss_mem.metadata
        if m.get("category") == "monologue"
        and m.get("timestamp", "") >= cutoff
    ][-MONOLOGUE_INJECT_MAX:]


async def monologue_loop(interval_minutes: int = None):
    if interval_minutes is None:
        interval_minutes = MONOLOGUE_INTERVAL_MINUTES

    await asyncio.sleep(MONOLOGUE_FIRST_DELAY_MINUTES * 60)
    while True:
        try:
            print(f"\n💭 [{datetime.now().strftime('%H:%M:%S')}] "
                  f"Внутренний монолог...")
            await think_once()
        except Exception as e:
            print(f"⚠️ Ошибка в цикле монолога: {e}")
        await asyncio.sleep(interval_minutes * 60)


def clear_all():
    """Очистить все монологи из FAISS и лога."""
    global faiss_mem
    faiss_mem.metadata = [
        m for m in faiss_mem.metadata if m.get("category") != "monologue"
    ]
    faiss_mem.save()
    if MONOLOGUE_LOG.exists():
        MONOLOGUE_LOG.unlink()