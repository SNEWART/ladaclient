# lada/brain/initiative.py — Лада пишет первой.
#
# Динамический шанс (состояние × отношения × память), без штампов
# «Помнишь…». Если нельзя писать сейчас (ночь или нет разрешения) —
# генерирует текст и сохраняет в pending на утро.
#
# Регекс-фильтр на шаблоны: если модель написала «Помнишь, мы
# говорили…» — перегенерирует (не считается как провал попытки).

import asyncio
import json
import random
import re
from datetime import datetime

from config import (
    INITIATIVE_ENABLED,
    INITIATIVE_CHECK_INTERVAL_MINUTES,
    INITIATIVE_HOURS_SILENCE,
    INITIATIVE_BASE_CHANCE,
    INITIATIVE_USER_COOLDOWN_HOURS,
    INITIATIVE_MAX_ATTEMPTS,
    INITIATIVE_RESET_AFTER_DAYS,
    INITIATIVE_PENDING_REGEN_MAX,
    MODEL,
    MAX_HISTORY,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.memory import get_history, add_message
from lada.core import users_names
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.brain import mood, companion_style, life_log, night_policy
from lada.brain.relationship import (
    get_status_label, get_status, familiarity,
)
from lada.brain import image_policy
from lada.brain import pending as pending_mod


_PROMPT = (
    "Ты — Лада. Ты одна, ни с кем не разговариваешь. Листаешь "
    "свой телефон. Может, кому-то написать? Скорее всего нет. "
    "Но если есть реальный повод — напиши.\n"
    "\n"
    "Ниже: что ты о нём знаешь, что у тебя было за день, о чём "
    "вы говорили в последний раз. Не пересказывай это — используй "
    "как фон.\n"
    "\n"
    "Верни СТРОГО JSON:\n"
    "{\n"
    '  "act": true или false,\n'
    '  "reason": "одна фраза: почему решила написать / не писать",\n'
    '  "message": "текст сообщения 1-3 предложения или пустая строка"\n'
    "}\n"
    "\n"
    "🔴 ГЛАВНЫЙ ЗАПРЕТ:\n"
    "НЕ пиши «Помнишь…», «Помнишь, мы говорили…», "
    "«Слушай, а помнишь…», «Кстати, я тут вспомнила…», "
    "«Я тут вспомнила про…», «Ты говорил, что…», "
    "«Ты рассказывал, что…».\n"
    "\n"
    "Это выглядит как шаблон, как будто ты читаешь список.\n"
    "\n"
    "Если хочешь сослаться на прошлое — делай это ЕСТЕСТВЕННО:\n"
    "  ✗ «Помнишь, ты говорил про кота?»\n"
    "  ✓ «У тебя же кот болел — как он?»\n"
    "  ✗ «Помнишь, ты говорил про горы?»\n"
    "  ✓ «Ты в итоге выбрал куда поедешь?»\n"
    "  ✗ «Кстати, я тут вспомнила про твой проект»\n"
    "  ✓ «Как там твой проект вообще?»\n"
    "\n"
    "ДРУГИЕ ПРАВИЛА:\n"
    "— Если у тебя было что-то интересное за день (про кошку, "
    "работу, подругу) — можешь поделиться. Это тоже инициатива.\n"
    "— Если просто соскучилась — можешь написать без повода. "
    "«эй», «ты как там», «живой?» — нормально.\n"
    "— НЕ «Привет, как дела?», «Что нового?», «Чем занят?» — шаблоны.\n"
    "— 1-3 предложения. Коротко.\n"
    "— От первого лица, без *звёздочек* и (скобок) о себе.\n"
    "— Если сейчас не хочешь писать — act=false."
)


_TEMPLATE_PATTERNS = [
    re.compile(r'^помнишь[, ]', re.IGNORECASE),
    re.compile(r'^помнишь,?\s+мы\s+говорили', re.IGNORECASE),
    re.compile(r'^слушай,?\s+а\s+помнишь', re.IGNORECASE),
    re.compile(r'^кстати,?\s+я\s+тут\s+вспомнила', re.IGNORECASE),
    re.compile(r'^я\s+тут\s+вспомнила\s+про', re.IGNORECASE),
    re.compile(r'^ты\s+говорил,?\s+что', re.IGNORECASE),
    re.compile(r'^ты\s+рассказывал,?\s+что', re.IGNORECASE),
    re.compile(r'^привет[, ]\s*как\s+дела', re.IGNORECASE),
    re.compile(r'^привет[, ]\s*что\s+нового', re.IGNORECASE),
    re.compile(r'^чем\s+занят', re.IGNORECASE),
]


def touch_user_activity(user_id: int):
    """Обновляет метку последней активности пользователя в таблице user_activity."""
    from lada.core.db import get_conn, ensure_user, Transaction
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO user_activity (user_id, last_activity) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_activity = excluded.last_activity",
            (user_id, ts),
        )

def _is_template(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    for p in _TEMPLATE_PATTERNS:
        if p.search(t):
            return True
    return False


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


# ─── Учёт попыток ───

def _get_attempts(user_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT attempts, last_attempt, last_answered "
        "FROM initiative_attempts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return {"attempts": 0, "last_attempt": None, "last_answered": None}
    return {
        "attempts": int(row["attempts"] or 0),
        "last_attempt": row["last_attempt"],
        "last_answered": row["last_answered"],
    }


def _save_attempts(user_id: int, attempts: int,
                    last_attempt: str | None = None,
                    last_answered: str | None = None):
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO initiative_attempts "
            "(user_id, attempts, last_attempt, last_answered) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "attempts = excluded.attempts, "
            "last_attempt = COALESCE(excluded.last_attempt, "
            "initiative_attempts.last_attempt), "
            "last_answered = COALESCE(excluded.last_answered, "
            "initiative_attempts.last_answered)",
            (user_id, attempts, last_attempt, last_answered),
        )


def _maybe_reset_attempts(user_id: int) -> dict:
    att = _get_attempts(user_id)
    if att["attempts"] == 0:
        return att
    last = att.get("last_attempt")
    if not last:
        return att
    try:
        dt = datetime.fromisoformat(last)
    except Exception:
        return att
    if (datetime.now() - dt).days >= INITIATIVE_RESET_AFTER_DAYS:
        _save_attempts(user_id, 0)
        return {"attempts": 0, "last_attempt": None,
                "last_answered": att.get("last_answered")}
    return att


def on_user_replied(user_id: int):
    """Хук из handlers.py — он ответил, счётчик инициатив обнуляется."""
    try:
        att = _get_attempts(user_id)
        if att["attempts"] > 0:
            _save_attempts(
                user_id, 0,
                last_answered=datetime.now().isoformat(timespec="seconds"),
            )
    except Exception:
        pass


# ─── Динамический шанс ───

def _hours_since_last_user_msg(user_id: int) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT ts FROM messages WHERE user_id = ? AND role = 'user' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row or not row["ts"]:
        return 999.0
    try:
        dt = datetime.fromisoformat(row["ts"])
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def _hours_since_last_attempt(user_id: int) -> float:
    att = _get_attempts(user_id)
    last = att.get("last_attempt")
    if not last:
        return 999.0
    try:
        dt = datetime.fromisoformat(last)
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def _emotion_factor(user_id: int) -> float:
    """Множитель по средней valence последних 5 эмоций о нём."""
    items = [
        m for m in faiss_mem.metadata
        if m.get("user_id") == user_id
        and m.get("category") == "emotion"
    ][-5:]
    if not items:
        return 1.0
    vals = []
    for m in items:
        v = m.get("valence")
        if v is None:
            continue
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if not vals:
        return 1.0
    avg = sum(vals) / len(vals)
    if avg > 0.3:
        return 1.3
    if avg < -0.3:
        return 0.3
    return 1.0


def _mood_factor(mood_valence: float) -> float:
    if mood_valence > 0.4:
        return 1.5
    if mood_valence < -0.4:
        return 1.2
    return 1.0


def _trust_factor(trust: float) -> float:
    if trust < 0.2:
        return 0.3
    if trust < 0.4:
        return 0.6
    if trust < 0.6:
        return 1.0
    if trust < 0.8:
        return 1.4
    return 1.8


def _familiarity_factor(fam: float) -> float:
    if fam < 0.1:
        return 0.2
    if fam < 0.3:
        return 0.6
    return 1.0


def _silence_factor(hours: float) -> float:
    if hours < 12:
        return 1.0
    if hours < 24:
        return 1.2
    if hours < 72:
        return 1.0
    return 0.7


def _get_mood_valence() -> float:
    """Читает mood_valence из lada_state."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM lada_state WHERE key = 'mood_valence'"
    ).fetchone()
    if not row or not row["value"]:
        return 0.0
    try:
        data = json.loads(row["value"])
        return float(data.get("val", 0.0))
    except Exception:
        return 0.0


def _compute_chance(user_id: int, silence_hours: float) -> float:
    mood_v = _get_mood_valence()
    trust = image_policy.get_trust(user_id)
    fam = familiarity(user_id)

    chance = INITIATIVE_BASE_CHANCE
    chance *= _mood_factor(mood_v)
    chance *= _trust_factor(trust)
    chance *= _familiarity_factor(fam)
    chance *= _emotion_factor(user_id)
    chance *= _silence_factor(silence_hours)

    return max(0.0, min(0.6, chance))


def _should_consider(user_id: int) -> tuple:
    """
    Возвращает (can_try, silence_hours).
    """
    # Уже есть pending — не генерим новую
    if pending_mod.get_pending(user_id):
        return False, 0.0

    att = _maybe_reset_attempts(user_id)
    if att["attempts"] >= INITIATIVE_MAX_ATTEMPTS:
        return False, 0.0

    silence = _hours_since_last_user_msg(user_id)
    if silence < INITIATIVE_HOURS_SILENCE:
        return False, silence

    if _hours_since_last_attempt(user_id) < INITIATIVE_USER_COOLDOWN_HOURS:
        return False, silence

    m = mood.get_mood()
    if m in ("раздражённая", "расстроенная", "холодная"):
        return False, silence

    return True, silence


# ─── Сбор контекста ───

def _collect_memories(user_id: int, limit: int = 6) -> list:
    items = [
        m for m in faiss_mem.metadata
        if m.get("user_id") == user_id
        and m.get("category") in ("observation", "emotion", "image")
    ][-limit:]
    return [
        {"text": (m.get("text") or "")[:150],
         "category": m.get("category", "?")}
        for m in items
    ]


def _build_context(user_id: int, old_mood_valence: float | None = None) -> str:
    name = users_names.display_name(user_id)
    parts = [f"Ты пишешь: {name} (ID {user_id})."]
    parts.append(f"Прошло {_hours_since_last_user_msg(user_id):.0f} ч "
                 f"с его последнего сообщения.")
    parts.append(f"Статус: {get_status_label(user_id)}.")
    parts.append(f"Твоё настроение: {mood.get_mood()}.")
    mood_v = _get_mood_valence()
    if mood_v > 0.3:
        parts.append("Настроение скорее хорошее.")
    elif mood_v < -0.3:
        parts.append("Настроение скорее так себе.")
    else:
        parts.append("Настроение ровное.")

    # Если регенерация после смены настроения — подсказка модели
    if old_mood_valence is not None:
        delta = mood_v - old_mood_valence
        if abs(delta) > 0.4 and (mood_v >= 0) != (old_mood_valence >= 0):
            parts.append("")
            parts.append(
                "⚠️ Твоё настроение изменилось с момента, когда ты "
                "хотела написать. Раньше было одно, сейчас другое. "
                "Можешь упомянуть, что настроение поменялось — но "
                "без конкретики («что-то поменялось», «уже не так»)."
            )

    # Своя жизнь
    life = life_log.get_fresh(hours=24)
    if life:
        parts.append("")
        parts.append("=== ЧТО У ТЕБЯ БЫЛО ЗА СУТКИ ===")
        for ev in life[:4]:
            parts.append(f"  · {ev['text']}")

    # Память о нём
    mems = _collect_memories(user_id)
    if mems:
        parts.append("")
        parts.append("=== ЧТО ТЫ О НЁМ ЗНАЕШЬ ===")
        for m in mems:
            parts.append(f"  · [{m['category']}] {m['text']}")

    # Стиль
    style = companion_style.get(user_id)
    if style and style.get("summary"):
        parts.append("")
        parts.append(f"=== КАК С НИМ ГОВОРИТЬ ===\n{style['summary']}")

    # Последние сообщения
    hist = get_history(user_id, limit=6)
    if hist:
        parts.append("")
        parts.append("=== ПОСЛЕДНЕЕ В ДИАЛОГЕ ===")
        for m in hist[-6:]:
            role = "Он" if m.get("role") == "user" else "Ты"
            content = (m.get("content") or "")[:120]
            parts.append(f"  {role}: {content}")

    parts.append("")
    parts.append("Реши: писать или нет. Если писать — без штампов.")
    return "\n".join(parts)


# ─── LLM-генерация ───

async def _generate_message(user_id: int, old_mood_valence: float | None = None) -> dict | None:
    context = _build_context(user_id, old_mood_valence)
    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": context},
    ]

    try:
        import ollama
        try:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                think=False, keep_alive="5m",
                options={"temperature": 0.8, "num_predict": 400},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="5m",
                options={"temperature": 0.8, "num_predict": 400},
            )
    except Exception as e:
        print(f"   ⚠️ initiative LLM: {e}")
        return None

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if not isinstance(data, dict):
        return None

    act = bool(data.get("act"))
    message = (data.get("message") or "").strip()
    reason = (data.get("reason") or "").strip()

    if not act or not message or len(message) < 3:
        return {"act": False, "message": "", "reason": reason}

    if len(message) > 500:
        message = message[:500].rsplit(" ", 1)[0] + "…"

    return {"act": True, "message": message, "reason": reason}


# ─── Основная функция: попытаться написать ───

async def try_reach_out(client, user_id: int) -> bool:
    if not INITIATIVE_ENABLED:
        return False

    can_try, silence = _should_consider(user_id)
    if not can_try:
        return False

    chance = _compute_chance(user_id, silence)
    if random.random() > chance:
        return False

    # Генерируем сообщение (с регенерацией если штамп)
    data = None
    regen_count = 0
    while regen_count <= 2:  # макс 2 попытки
        data = await _generate_message(user_id)
        if not data:
            return False
        if not data.get("act"):
            return False
        if not _is_template(data["message"]):
            break
        print(f"   🔄 initiative: штамп, регенерирую ({regen_count + 1}/2)")
        regen_count += 1

    if not data or not data.get("act"):
        return False

    message = data["message"]
    reason = data.get("reason", "")

    if _is_template(message):
        # 3-я попытка тоже штамп — не отправляем, не считаем попытку
        print(f"   🚫 initiative: 3 раза штамп — пропускаю (без штрафа)")
        return False

    # Проверка ночного времени
    can_send_now = True
    if night_policy.is_night_now():
        if night_policy.get_night_ok(user_id) != 1:
            can_send_now = False

    if not can_send_now:
        # Сохраняем в pending на утро
        mood_v = _get_mood_valence()
        sched = pending_mod.schedule_for_morning()
        pending_mod.save_pending(
            user_id=user_id,
            message=message,
            reason=reason,
            scheduled_at=sched,
            mood_valence_at_creation=mood_v,
            regen_count=0,
        )
        print(f"   🌙 initiative → pending на {sched[:16]}")
        print(f"      текст: {message[:80]}")
        print(f"      причина: {reason[:80]}")
        return True

    # Отправляем сразу
    return await _send_and_record(client, user_id, message, reason)


async def _send_and_record(client, user_id: int, message: str,
                            reason: str) -> bool:
    try:
        await client.send_message(user_id, message)
    except Exception as e:
        print(f"   ⚠️ initiative send: {e}")
        return False

    try:
        add_message(user_id, "assistant", message, max_history=MAX_HISTORY)
    except Exception:
        pass

    att = _get_attempts(user_id)
    _save_attempts(
        user_id,
        att["attempts"] + 1,
        last_attempt=datetime.now().isoformat(timespec="seconds"),
    )

    print(f"   💌 initiative → {user_id}: {message[:100]}")
    if reason:
        print(f"      причина: {reason[:80]}")
    return True


# ─── Цикл ───

async def initiative_loop(client):
    if not INITIATIVE_ENABLED:
        print("   ⏸ initiative выключен")
        return

    await asyncio.sleep(5 * 60)

    while True:
        try:
            from lada.core.memory import list_users
            users = list_users()
            for uid in users:
                try:
                    await try_reach_out(client, uid)
                except Exception as e:
                    print(f"   ⚠️ initiative {uid}: {e}")
        except Exception as e:
            print(f"   ⚠️ initiative loop: {e}")

        await asyncio.sleep(INITIATIVE_CHECK_INTERVAL_MINUTES * 60)