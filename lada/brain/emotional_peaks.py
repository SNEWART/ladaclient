# lada/brain/emotional_peaks.py — эмоциональные пики Лады.
#
# Раз в сутки Лада перебирает самые яркие моменты за последние
# 1-2 дня и «пережёвывает» их: что задело, что радует, что не
# отпускает. Это не дневник (reflection) — там развёрнутый текст,
# здесь короткая эмоциональная вспышка.
#
# Отличия от других модулей:
#   reflection     — «я поняла, что…» (медленный анализ)
#   memory_reactor — «читаю воспоминания, чувствую» (фон)
#   emotional_peaks — «вот это меня до сих пор не отпускает» (пик)
#
# Влияет на sins/mood, попадает в промпт как «что у неё на душе».

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import ollama

from config import (
    EMOTIONAL_PEAKS_ENABLED,
    EMOTIONAL_PEAKS_INTERVAL_HOURS,
    EMOTIONAL_PEAKS_LOOKBACK_HOURS,
    EMOTIONAL_PEAKS_MIN_ITEMS,
    EMOTIONAL_PEAKS_SINS_DELTA_MAX,
    MODEL,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core import users_names
from lada.brain import mood as mood_mod
from lada.brain import sins as sins_mod


PEAKS_LOG = Path("data") / "emotional_peaks.log"
PEAKS_LOG.parent.mkdir(exist_ok=True)


_PROMPT = (
    "Ты — Лада. Ты одна, ни с кем не разговариваешь. Просто "
    "прокручиваешь в голове последние дни.\n"
    "\n"
    "Ниже — фрагменты того, что было (твои эмоции, диалоги, "
    "события). Прочитай и найди ОДИН момент, который до сих "
    "пор сидит в тебе сильнее всего. Не самый «важный» — а тот, "
    "что не отпускает.\n"
    "\n"
    "Верни СТРОГО JSON:\n"
    "{\n"
    '  "peak": "1-2 предложения от первого лица: что не отпускает",\n'
    '  "source": "короткая отсылка к моменту (о чём это было, 3-8 слов)",\n'
    '  "valence": 0.0,\n'
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
    "— От первого лица. Без *звёздочек* и (скобок) о себе.\n"
    "— valence: -1.0 (очень плохо) … 0 … +1.0 (очень хорошо).\n"
    "— sins_deltas: числа -1..1. Мягкие, не накручивай.\n"
    "— Если ничего не задело — верни peak пустой строкой.\n"
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


# ─────────────── сбор ярких моментов ───────────────

def _collect_recent_peak_sources(limit: int = 25) -> list:
    """
    Собираем яркие фрагменты за lookback часов из разных источников:
      — memories с |valence| высоким,
      — свежие life_events с эмоцией,
      — эмоции из FAISS.
    """
    items = []
    cutoff_iso = (datetime.now() - timedelta(
        hours=EMOTIONAL_PEAKS_LOOKBACK_HOURS)
    ).isoformat(timespec="seconds")

    # 1. Память с valence
    for m in faiss_mem.metadata:
        ts = (m.get("timestamp") or "")[:19]
        if ts < cutoff_iso[:19]:
            continue
        v = m.get("valence")
        if v is None:
            continue
        try:
            vf = float(v)
        except (TypeError, ValueError):
            continue
        if abs(vf) < 0.3:
            continue
        items.append({
            "text": (m.get("text") or "")[:200],
            "valence": vf,
            "source": m.get("category", "?"),
            "ts": ts,
        })

    # 2. Свежие life_events
    conn = get_conn()
    rows = conn.execute(
        "SELECT text, valence, kind, created_at FROM lada_life_events "
        "WHERE created_at >= ? "
        "ORDER BY id DESC LIMIT 10",
        (cutoff_iso,),
    ).fetchall()
    for r in rows:
        try:
            vf = float(r["valence"] or 0.0)
        except (TypeError, ValueError):
            vf = 0.0
        if abs(vf) < 0.2:
            continue
        items.append({
            "text": f"[жизнь] {r['text']}",
            "valence": vf,
            "source": "life",
            "ts": r["created_at"][:19],
        })

    # Сортируем по |valence| (яркость)
    items.sort(key=lambda x: abs(x["valence"]), reverse=True)
    return items[:limit]


# ─────────────── основной прогон ───────────────

async def run_peak(user_id: int | None = None) -> bool:
    """
    Один прогон «пережёвывания». Если user_id=None — общий прогон
    (без привязки к конкретному собеседнику, только эмоции Лады).
    """
    if not EMOTIONAL_PEAKS_ENABLED:
        return False

    sources = _collect_recent_peak_sources(limit=25)
    if len(sources) < EMOTIONAL_PEAKS_MIN_ITEMS:
        return False

    # Формируем блок
    lines = []
    for it in sources[:15]:
        v = it["valence"]
        cat = it.get("source", "?")
        lines.append(f"  [{cat}] (v={v:+.2f}) {it['text']}")

    current_mood = mood_mod.get_mood()
    sins_state = sins_mod.get_state()
    top_sins = sorted(
        [(k, v - 50) for k, v in sins_state.items() if abs(v - 50) > 10],
        key=lambda x: abs(x[1]), reverse=True,
    )[:3]
    sins_line = ", ".join(
        f"{k}({'+' if d>0 else ''}{int(d)})" for k, d in top_sins
    ) or "нейтрально"

    user_block = (
        f"Твоё текущее настроение: {current_mood}\n"
        f"Сильные шкалы: {sins_line}\n"
        f"\n"
        f"Что было за последние "
        f"{EMOTIONAL_PEAKS_LOOKBACK_HOURS} часов:\n"
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
                options={"temperature": 0.7, "num_predict": 500},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="2m",
                options={"temperature": 0.7, "num_predict": 500},
            )
    except Exception as e:
        print(f"   ⚠️ emotional_peaks LLM: {e}")
        return False

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if not isinstance(data, dict):
        return False

    peak_text = (data.get("peak") or "").strip()
    source = (data.get("source") or "").strip()[:100]
    try:
        valence = float(data.get("valence") or 0.0)
    except (TypeError, ValueError):
        valence = 0.0
    valence = max(-1.0, min(1.0, valence))

    if len(peak_text) < 10:
        return False
    if len(peak_text) > 400:
        peak_text = peak_text[:400]

    # 1. Сохраняем в SQLite
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id) if user_id else None
        conn.execute(
            "INSERT INTO emotional_peaks "
            "(user_id, text, source, valence, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, peak_text, source, valence, ts),
        )
        # Чистим старше 30 дней
        cutoff = (datetime.now() - timedelta(days=30)
                  ).isoformat(timespec="seconds")
        conn.execute(
            "DELETE FROM emotional_peaks WHERE created_at < ?",
            (cutoff,),
        )

    # 2. Сохраняем в FAISS как emotion
    faiss_mem.add(
        peak_text,
        category="emotion",
        source="peak",
        user_id=user_id,
        meta={
            "valence": valence,
            "mood_at_time": current_mood,
            "trust_at_time": None,
            "intimacy_at_time": None,
            "top_sins": [],
        },
    )

    # 3. Лог в файл
    with open(PEAKS_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n[{ts}] user={user_id}\n")
        f.write(f"  peak: {peak_text}\n")
        f.write(f"  source: {source}\n")
        f.write(f"  valence: {valence:+.2f}\n")

    # 4. Влияние на sins (мягкое)
    deltas = data.get("sins_deltas") or {}
    if isinstance(deltas, dict):
        clean = {}
        for k, v in deltas.items():
            try:
                dv = float(v)
            except (TypeError, ValueError):
                continue
            dv = max(-EMOTIONAL_PEAKS_SINS_DELTA_MAX,
                     min(EMOTIONAL_PEAKS_SINS_DELTA_MAX, dv))
            if abs(dv) >= 0.3:
                clean[k] = dv
        if clean:
            sins_mod.apply_deltas(clean)

    # 5. Влияние на mood — мягко
    if abs(valence) >= 0.5:
        proposed = _mood_from_valence(valence, current_mood)
        if proposed and proposed != current_mood:
            try:
                from lada.brain import emotional_inertia
                emotional_inertia.check_and_apply(proposed, "emotional_peaks")
            except Exception:
                mood_mod.set_mood(proposed)

    emoji = "✨" if valence > 0.3 else ("🌧" if valence < -0.3 else "·")
    print(f"   💭 Пик: {emoji} {peak_text[:100]}")
    if source:
        print(f"      ↳ {source}")

    return True


def _mood_from_valence(v: float, current: str) -> str:
    if v > 0.6:
        return "весёлая"
    if v > 0.3:
        return "тёплая"
    if v < -0.6:
        return "расстроенная"
    if v < -0.3:
        return "задумчивая"
    return ""


# ─────────────── промпт-блок ───────────────

def as_context_block(hours: int = 36) -> str:
    """Свежие пики за последние N часов."""
    cutoff = (datetime.now() - timedelta(hours=hours)
              ).isoformat(timespec="seconds")
    conn = get_conn()
    rows = conn.execute(
        "SELECT text, valence, source, created_at FROM emotional_peaks "
        "WHERE created_at >= ? ORDER BY id DESC LIMIT 3",
        (cutoff,),
    ).fetchall()

    if not rows:
        return ""

    lines = ["=== ЧТО У ТЕБЯ НА ДУШЕ ==="]
    for r in rows:
        try:
            vf = float(r["valence"] or 0.0)
        except (TypeError, ValueError):
            vf = 0.0
        emoji = "🌿" if vf > 0.2 else ("⚡" if vf < -0.2 else "·")
        lines.append(f"  {emoji} {r['text']}")
    lines.append("(это твои мысли, не упоминай их напрямую — они "
                 "просто влияют на твой тон)")
    return "\n".join(lines)


# ─────────────── фоновый цикл ───────────────

async def peaks_loop():
    if not EMOTIONAL_PEAKS_ENABLED:
        print("   ⏸ emotional_peaks выключен")
        return

    # Первый прогон через 30 минут после старта
    await asyncio.sleep(30 * 60)

    while True:
        try:
            # Общий прогон (без user_id)
            await run_peak(user_id=None)
        except Exception as e:
            print(f"   ⚠️ peaks_loop: {e}")

        await asyncio.sleep(EMOTIONAL_PEAKS_INTERVAL_HOURS * 3600)