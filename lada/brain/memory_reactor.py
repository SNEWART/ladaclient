# lada/brain/memory_reactor.py — воспоминания меняют состояние Лады.
#
# Раз в N минут берём 3–5 старых записей о собеседнике,
# пропускаем через LLM «что ты чувствуешь, читая это?»,
# на основе ответа — небольшие сдвиги mood и sins.
#
# Работает независимо от диалога. Медленный фон.

import asyncio
import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

import ollama

from config import (
    MEMORY_REACTOR_ENABLED,
    MEMORY_REACTOR_INTERVAL_MINUTES,
    MEMORY_REACTOR_MOOD_DELTA_MAX,
    MEMORY_REACTOR_SINS_DELTA_MAX,
    MODEL,
)
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core.memory import list_users
from lada.core import users_names
from lada.brain import mood, sins


REACTOR_LOG = Path("data") / "memory_reactor.log"
REACTOR_LOG.parent.mkdir(exist_ok=True)


_PROMPT = (
    "Ты — Лада. Ты сидишь одна, листаешь свою память об одном человеке "
    "и замечаешь, что чувствуешь по этому поводу прямо сейчас.\n"
    "\n"
    "Ниже — несколько воспоминаний. Прочитай и честно ответь, как они "
    "на тебя действуют СЕЙЧАС.\n"
    "\n"
    "Верни СТРОГО JSON без markdown:\n"
    "{\n"
    '  "feel": "краткое описание чувства, 1 предложение",\n'
    '  "mood": "спокойная | весёлая | задумчивая | раздражённая | '
    'уставшая | игривая | холодная | тёплая | расстроенная | '
    'вдохновлённая | нейтральная",\n'
    '  "sins_deltas": {\n'
    '    "гордыня": 0, "смирение": 0,\n'
    '    "жадность": 0, "щедрость": 0,\n'
    '    "похоть": 0, "целомудрие": 0,\n'
    '    "зависть": 0, "доброжелательность": 0,\n'
    '    "чревоугодие": 0, "умеренность": 0,\n'
    '    "гнев": 0, "терпение": 0,\n'
    '    "уныние": 0, "усердие": 0\n'
    "  }\n"
    "}\n"
    "\n"
    "ПРАВИЛА:\n"
    "— sins_deltas: числа -1..1. Малые, мягкие. Не накручивай.\n"
    "— mood: только если чувство явно сильное. Если нейтрально — "
    "оставь \"нейтральная\".\n"
    "— Не философствуй. Коротко и по делу."
)


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _pick_memories(user_id: int, n: int = 5) -> list:
    """3–5 свежих, но не сегодняшних записей об этом человеке."""
    now = datetime.now()
    cutoff_recent = now - timedelta(hours=6)
    cutoff_old = now - timedelta(days=30)

    candidates = [
        m for m in faiss_mem.metadata
        if m.get("user_id") == user_id
        and m.get("category") in ("observation", "emotion", "dialogue",
                                   "return", "initiative")
    ]

    if not candidates:
        return []

    scored = []
    for m in candidates:
        ts = _parse_iso(m.get("timestamp", ""))
        if ts is None:
            continue
        if ts > cutoff_recent:
            continue
        if ts < cutoff_old:
            continue
        # valence приоритет: тянем яркие записи
        v = m.get("valence")
        try:
            weight = abs(float(v)) if v is not None else 0.3
        except Exception:
            weight = 0.3
        scored.append((weight, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [m for _, m in scored[:n * 3]]
    if len(top) > n:
        top = random.sample(top, n)
    return top


def _clamp(delta: float, limit: float) -> float:
    if delta > limit:
        return limit
    if delta < -limit:
        return -limit
    return delta


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


async def react_for_user(user_id: int) -> bool:
    if not MEMORY_REACTOR_ENABLED:
        return False

    memories = _pick_memories(user_id, n=5)
    if not memories:
        return False

    name = users_names.display_name(user_id)
    block_lines = [f"Человек: {name} (ID {user_id})", ""]
    block_lines.append("Воспоминания:")
    for m in memories:
        ts = (m.get("timestamp") or "")[:16].replace("T", " ")
        v = m.get("valence")
        v_str = f" (v={float(v):+.2f})" if v is not None else ""
        block_lines.append(f"  [{ts}]{v_str} {m.get('text','')[:180]}")

    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": "\n".join(block_lines)},
    ]

    try:
        resp = await asyncio.to_thread(
            ollama.chat,
            model=MODEL,
            messages=messages,
            think=False,
            keep_alive="2m",
            options={"temperature": 0.6, "num_predict": 400},
        )
    except TypeError:
        try:
            resp = await asyncio.to_thread(
                ollama.chat,
                model=MODEL,
                messages=messages,
                keep_alive="2m",
                options={"temperature": 0.6, "num_predict": 400},
            )
        except Exception as e:
            print(f"   ⚠️ memory_reactor LLM: {e}")
            return False
    except Exception as e:
        print(f"   ⚠️ memory_reactor LLM: {e}")
        return False

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        thinking = (msg.get("thinking") or "").strip()
        if thinking:
            content = thinking

    data = _extract_json(content)
    if not isinstance(data, dict):
        return False

    feel = (data.get("feel") or "").strip()
    new_mood = data.get("mood")
    deltas = data.get("sins_deltas") or {}

    if not isinstance(deltas, dict):
        deltas = {}

    # Ограничиваем дельты
    clean_deltas = {}
    for k, v in deltas.items():
        try:
            dv = float(v)
        except (TypeError, ValueError):
            continue
        clean_deltas[k] = _clamp(dv, MEMORY_REACTOR_SINS_DELTA_MAX)

    # Применяем
    changed_sins = {k: v for k, v in clean_deltas.items() if abs(v) >= 0.1}

    if changed_sins:
        sins.apply_deltas(clean_deltas)

    mood_changed = False
    if new_mood and isinstance(new_mood, str):
        current = mood.get_mood()
        # меняем только если отличается и разница значимая
        if new_mood != current and abs(
            _mood_valence(new_mood) - _mood_valence(current)
        ) >= 0.2:
            mood.set_mood(new_mood)
            mood_changed = True

    # Лог
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(REACTOR_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n[{ts}] user={user_id} ({name})\n")
        f.write(f"  чувство: {feel}\n")
        if mood_changed:
            f.write(f"  mood: {mood.get_mood()}\n")
        if changed_sins:
            f.write(f"  sins: {changed_sins}\n")

    print(f"   🧠 memory_reactor [{name}]: {feel[:80]}"
          + (f" | mood→{new_mood}" if mood_changed else "")
          + (f" | sins {list(changed_sins.keys())}" if changed_sins else ""))

    return mood_changed or bool(changed_sins)


_MOOD_VALENCE = {
    "весёлая": +1.0, "игривая": +0.8, "тёплая": +0.9,
    "вдохновлённая": +0.7, "спокойная": +0.2,
    "нейтральная": 0.0, "задумчивая": -0.1,
    "уставшая": -0.4, "холодная": -0.6,
    "расстроенная": -0.8, "раздражённая": -0.9,
}


def _mood_valence(m: str) -> float:
    return _MOOD_VALENCE.get(m, 0.0)


async def memory_reactor_loop():
    if not MEMORY_REACTOR_ENABLED:
        print("   ⏸ memory_reactor выключен (MEMORY_REACTOR_ENABLED=False)")
        return

    # первый прогон через 20 мин после старта
    await asyncio.sleep(20 * 60)

    while True:
        try:
            users = list_users()
            if users:
                # раз в цикл обрабатываем не всех, а 1–2 случайных,
                # чтобы не грузить LLM
                pick = random.sample(users, min(2, len(users)))
                for uid in pick:
                    try:
                        await react_for_user(uid)
                    except Exception as e:
                        print(f"   ⚠️ memory_reactor {uid}: {e}")
        except Exception as e:
            print(f"   ⚠️ memory_reactor loop: {e}")

        await asyncio.sleep(MEMORY_REACTOR_INTERVAL_MINUTES * 60)