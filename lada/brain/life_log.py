# lada/brain/life_log.py — собственная жизнь Лады.
#
# Раз в N часов LLM придумывает ОДНО событие, которое произошло
# с ней вне чата: бытовое, с кошкой, с работой, с подругой.
# Событие живёт TTL часов, влияет на mood, попадает в промпт.
#
# Идея: у живой девушки есть жизнь ЗА ПРЕДЕЛАМИ переписки.
# Без этого Лада отвечает, но не «живёт между сообщениями».

import asyncio
import json
import re
from datetime import datetime, timedelta

import ollama

from config import (
    LIFE_LOG_ENABLED,
    LIFE_LOG_INTERVAL_HOURS,
    LIFE_LOG_MAX_ACTIVE,
    LIFE_LOG_TTL_HOURS,
    LIFE_LOG_MOOD_INFLUENCE,
    MODEL,
)
from lada.core.db import get_conn, Transaction
from lada.brain import mood as mood_mod
from lada.brain import sins as sins_mod


_PROMPT = (
    "Ты — Лада, 22 года. Ты не в чате сейчас — ты в своей жизни. "
    "Недавно произошло что-то обычное, что бывает у живого человека.\n"
    "\n"
    "Придумай ОДНО короткое событие, которое случилось у тебя "
    "за последние несколько часов. Категории:\n"
    "  home    — дома (пролила кофе, нашла старые вещи, разбила чашку)\n"
    "  cat     — с кошкой Мусей (уронила вазу, спит на клавиатуре)\n"
    "  work    — с работой (задержалась, скучный созвон, тупик в задаче)\n"
    "  social  — с подругой / знакомым (созвонились, поругались)\n"
    "  health  — со здоровьем (голова разболелась, не выспалась)\n"
    "  mood    — просто настроение (внезапно грустно, что-то вспомнила)\n"
    "\n"
    "Верни СТРОГО JSON:\n"
    "{\n"
    '  "text": "одна короткая фраза от первого лица (5-15 слов)",\n'
    '  "kind": "home | cat | work | social | health | mood",\n'
    '  "valence": 0.0\n'
    "}\n"
    "\n"
    "ПРАВИЛА:\n"
    "— От первого лица: «я пролила кофе», «Муся уронила вазу».\n"
    "— НЕ пиши про чат, про собеседника, про сообщения.\n"
    "— НЕ пиши философию. Только конкретное событие.\n"
    "— valence: -1.0 (очень плохо) … 0 (нейтрально) … +1.0 (очень хорошо).\n"
    "— Примеры:\n"
    "    «пролила кофе на ноутбук, сижу злая» — valence -0.5, home\n"
    "    «Муся уронила цветок с подоконника» — valence -0.3, cat\n"
    "    «нашла старую песню, залипла на повторе» — valence +0.6, mood\n"
    "    «задержалась на работе, устала» — valence -0.4, work\n"
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


# ─────────────── запись и чтение ───────────────

def _recent_texts(limit: int = 10) -> list:
    """Последние события — чтобы не повторяться."""
    cutoff = (datetime.now() - timedelta(hours=LIFE_LOG_TTL_HOURS)
              ).isoformat(timespec="seconds")
    conn = get_conn()
    rows = conn.execute(
        "SELECT text FROM lada_life_events "
        "WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [r["text"] for r in rows]


def _save(text: str, kind: str, valence: float):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        conn.execute(
            "INSERT INTO lada_life_events (text, kind, valence, created_at) "
            "VALUES (?, ?, ?, ?)",
            (text.strip()[:200], kind, float(valence), ts),
        )
        # Чистим очень старые записи (старше 7 дней)
        cutoff = (datetime.now() - timedelta(days=7)
                  ).isoformat(timespec="seconds")
        conn.execute(
            "DELETE FROM lada_life_events WHERE created_at < ?",
            (cutoff,),
        )


def get_active(limit: int = None) -> list:
    """События за последние TTL часов."""
    if limit is None:
        limit = LIFE_LOG_MAX_ACTIVE
    cutoff = (datetime.now() - timedelta(hours=LIFE_LOG_TTL_HOURS)
              ).isoformat(timespec="seconds")
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, text, kind, valence, created_at, consumed "
        "FROM lada_life_events WHERE created_at >= ? "
        "ORDER BY id DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_fresh(hours: int = 12) -> list:
    """Свежие события (за последние N часов)."""
    cutoff = (datetime.now() - timedelta(hours=hours)
              ).isoformat(timespec="seconds")
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, text, kind, valence, created_at "
        "FROM lada_life_events WHERE created_at >= ? "
        "ORDER BY id DESC",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


# ─────────────── генерация ───────────────

async def generate_new(force: bool = False) -> bool:
    if not LIFE_LOG_ENABLED and not force:
        return False

    recent = _recent_texts(limit=8)
    recent_block = "\n".join(f"  · {t}" for t in recent) if recent else "  (пока ничего)"

    m = mood_mod.get_mood()
    sins_state = sins_mod.get_state()
    top_sins = sorted(
        [(k, v - 50) for k, v in sins_state.items() if abs(v - 50) > 10],
        key=lambda x: abs(x[1]), reverse=True,
    )[:3]
    sins_line = ", ".join(f"{k}({'+' if d>0 else ''}{int(d)})"
                          for k, d in top_sins) or "нейтрально"

    user_block = (
        f"Твоё текущее настроение: {m}\n"
        f"Твои сильные шкалы: {sins_line}\n"
        f"\n"
        f"Недавно у тебя было (НЕ повторяй):\n{recent_block}\n"
        f"\n"
        f"Придумай новое событие."
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
                options={"temperature": 0.9, "num_predict": 200},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="2m",
                options={"temperature": 0.9, "num_predict": 200},
            )
    except Exception as e:
        print(f"   ⚠️ life_log LLM: {e}")
        return False

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if not isinstance(data, dict):
        return False

    text = (data.get("text") or "").strip()
    kind = (data.get("kind") or "home").strip().lower()
    try:
        valence = float(data.get("valence") or 0.0)
    except (TypeError, ValueError):
        valence = 0.0

    if len(text) < 5 or len(text) > 200:
        return False
    if kind not in ("home", "cat", "work", "social", "health", "mood"):
        kind = "home"
    valence = max(-1.0, min(1.0, valence))

    _save(text, kind, valence)

    emoji = "✨" if valence > 0.3 else ("💢" if valence < -0.3 else "·")
    print(f"   🌱 Событие: {emoji} {text} "
          f"[{kind}, v={valence:+.2f}]")

    return True


# ─────────────── влияние на mood ───────────────

def apply_to_mood():
    """
    Применяет valence непотреблённых событий к mood.
    Раз в цикл — вызывается из life_loop после generate_new.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, valence FROM lada_life_events "
        "WHERE consumed = 0 "
        "ORDER BY id"
    ).fetchall()

    if not rows:
        return

    # Суммируем valence и ограничиваем суммарный сдвиг
    total_valence = 0.0
    ids = []
    for r in rows:
        try:
            total_valence += float(r["valence"] or 0.0)
            ids.append(r["id"])
        except Exception:
            pass

    if not ids:
        return

    # Сдвигаем mood, если суммарная valence значима
    current = mood_mod.get_mood()
    if abs(total_valence) >= 0.4:
        from lada.brain.emotional_inertia import _valence as _mv
        cur_v = _mv(current)
        proposed = _propose_mood(cur_v + total_valence * LIFE_LOG_MOOD_INFLUENCE)
        if proposed and proposed != current:
            try:
                from lada.brain import emotional_inertia
                emotional_inertia.check_and_apply(proposed, "life_log")
            except Exception:
                mood_mod.set_mood(proposed)
            print(f"   🎭 Жизнь влияет на настроение: "
                  f"{current} → {mood_mod.get_mood()}")

    # Помечаем как consumed
    with Transaction() as conn2:
        for i in ids:
            conn2.execute(
                "UPDATE lada_life_events SET consumed = 1 WHERE id = ?",
                (i,),
            )


def _propose_mood(valence_sum: float) -> str:
    if valence_sum > 0.6:
        return "весёлая"
    if valence_sum > 0.3:
        return "тёплая"
    if valence_sum > 0.1:
        return "спокойная"
    if valence_sum < -0.6:
        return "расстроенная"
    if valence_sum < -0.3:
        return "задумчивая"
    if valence_sum < -0.1:
        return "уставшая"
    return ""


# ─────────────── промпт-блок ───────────────

def as_context_block() -> str:
    """Короткий блок «что у тебя произошло»."""
    fresh = get_fresh(hours=24)
    if not fresh:
        return ""

    lines = ["=== ЧТО У ТЕБЯ ПРОИЗОШЛО НЕДАВНО ==="]
    for ev in fresh[:4]:
        kind = ev.get("kind") or ""
        v = ev.get("valence")
        try:
            vf = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            vf = 0.0
        emoji = "🌿" if vf > 0.3 else ("⚡" if vf < -0.3 else "·")
        lines.append(f"  {emoji} {ev['text']}")
    lines.append("(Это твой реальный фон. Можешь упомянуть, если уместно, "
                 "но не навязчиво.)")
    return "\n".join(lines)


# ─────────────── фоновый цикл ───────────────

async def life_loop():
    if not LIFE_LOG_ENABLED:
        print("   ⏸ life_log выключен")
        return

    # Первое событие через 3 минуты после старта
    await asyncio.sleep(3 * 60)

    while True:
        try:
            # Сначала применяем прошлые события к mood, потом генерим новое
            apply_to_mood()
            await generate_new()
        except Exception as e:
            print(f"   ⚠️ life_loop: {e}")

        await asyncio.sleep(LIFE_LOG_INTERVAL_HOURS * 3600)