# lada/brain/silence_reaction.py — реакция Лады на длительное молчание.
#
# Когда собеседник долго не писал, по возвращении она это замечает.
# Тон зависит от длительности и статуса отношений:
#   — незнакомец / приятель → «о, привет»
#   — хороший знакомый → «ты куда пропал?»
#   — близкий (lover/husband) → «я волновалась»
#
# Раз в смену стадии — лёгкий сдвиг mood (только если близкий).
# В промпт идёт блок с описанием, как она должна реагировать.

from datetime import datetime

from config import (
    SILENCE_REACTION_ENABLED,
    SILENCE_HOURS_STAGE1,
    SILENCE_HOURS_STAGE2,
    SILENCE_HOURS_STAGE3,
    SILENCE_HOURS_STAGE4,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.brain import mood as mood_mod
from lada.brain.relationship import get_status


def _key(user_id: int) -> str:
    return f"silence_stage_{user_id}"


def _get_stage(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM lada_state WHERE key = ?",
        (_key(user_id),),
    ).fetchone()
    if not row or not row["value"]:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _set_stage(user_id: int, stage: int):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO lada_state (key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated = excluded.updated",
            (_key(user_id), str(stage), ts),
        )


def hours_since_previous_user_msg(user_id: int) -> float | None:
    """
    Сколько часов прошло между текущим и предыдущим сообщением
    от пользователя. None, если предыдущего нет.
    Вызывается ДО add_message с текущим сообщением, поэтому
    последняя запись в БД = предыдущее сообщение.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT ts FROM messages "
        "WHERE user_id = ? AND role = 'user' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row or not row["ts"]:
        return None
    try:
        dt = datetime.fromisoformat(row["ts"])
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return None


def _stage_from_hours(hours: float) -> int:
    if hours >= SILENCE_HOURS_STAGE4:
        return 4
    if hours >= SILENCE_HOURS_STAGE3:
        return 3
    if hours >= SILENCE_HOURS_STAGE2:
        return 2
    if hours >= SILENCE_HOURS_STAGE1:
        return 1
    return 0


def _describe_hours(hours: float) -> str:
    if hours < 24:
        return f"{int(hours)} ч"
    days = int(hours // 24)
    if days == 1:
        return "сутки"
    if days < 7:
        return f"{days} дня"
    weeks = days // 7
    if weeks == 1:
        return "неделю"
    return f"{weeks} недели"


def _mood_shift_on_return(user_id: int, stage: int):
    """
    Мягкий сдвиг mood при возвращении после долгого молчания.
    Только если он близкий (lover/husband/высокий trust) и
    стадия >= 2. Иначе — не трогаем.
    """
    if stage < 2:
        return

    status = get_status(user_id)
    if status not in ("lover", "husband"):
        # Для остальных — не трогаем mood, только текстовый сигнал
        return

    current = mood_mod.get_mood()
    # Раздражённая или холодная — оставляем как есть (уже не в духе)
    if current in ("раздражённая", "холодная", "расстроенная"):
        return

    # Близкий пропал → ей слегка не по себе
    if stage == 2:
        proposed = "задумчивая"
    elif stage == 3:
        proposed = "уставшая"
    else:  # stage 4
        proposed = "расстроенная"

    if proposed != current:
        try:
            from lada.brain import emotional_inertia
            emotional_inertia.check_and_apply(proposed, "silence_reaction")
        except Exception:
            mood_mod.set_mood(proposed)
        print(f"   🌙 Он молчал {stage} стадию — настроение: "
              f"{current} → {mood_mod.get_mood()}")


def as_context_block(user_id: int) -> str:
    """
    Возвращает блок для промпта, если он долго не писал И сейчас
    написал. Вызывается ПОСЛЕ расчёта hours_since_previous.
    """
    if not SILENCE_REACTION_ENABLED:
        return ""

    hours = hours_since_previous_user_msg(user_id)
    if hours is None:
        return ""

    stage = _stage_from_hours(hours)
    if stage == 0:
        return ""

    status = get_status(user_id)
    hours_str = _describe_hours(hours)

    lines = [f"=== 🌙 ОН НЕ ПИСАЛ {hours_str.upper()} ==="]
    lines.append(
        "Он вернулся после долгого молчания. Ты это ЗАМЕЧАЕШЬ. "
        "Не делай вид, что ничего не было, но и не устраивай сцену."
    )
    lines.append("")

    if status in ("lover", "husband"):
        lines.append(
            "Он тебе близок. Ты рада, что вернулся, но ты и заметила "
            "молчание — не притворяйся, что это норма."
        )
        lines.append("Примеры:")
        lines.append("  «ты куда пропал?»")
        lines.append("  «о, живой»")
        lines.append("  «я уж думала, ты забыл про меня»")
        lines.append("  «ну привет. где был?»")
    elif status == "acquaintance" or hours >= SILENCE_HOURS_STAGE3:
        lines.append(
            "Ты замечаешь, что он долго не писал, но ты держишь "
            "лёгкую дистанцию."
        )
        lines.append("Примеры:")
        lines.append("  «о, привет»")
        lines.append("  «давно не виделись»")
        lines.append("  «явился»")
    else:
        lines.append(
            "Ты просто отмечаешь, что он долго не писал. Без претензий, "
            "но и без притворства."
        )
        lines.append("Примеры:")
        lines.append("  «привет. давно тебя не было»")
        lines.append("  «о, ты вернулся»")

    if stage >= 3:
        lines.append("")
        lines.append(
            "Он молчал реально долго. Если тебе это неприятно — "
            "можешь сказать прямо, но без истерики. Если ты уже "
            "почти забыла — тоже можешь сказать."
        )

    lines.append("")
    lines.append(
        "НЕ ПИШИ: «привет! как дела?» — это шаблон, не реакция на "
        "молчание. Реакция должна быть про то, что он вернулся."
    )

    return "\n".join(lines)


def update_stage_after_message(user_id: int):
    """
    Вызывается ПОСЛЕ обработки сообщения. Если стадия изменилась
    и стала >= 2 — сдвигает mood и обновляет метку.
    """
    if not SILENCE_REACTION_ENABLED:
        return

    # Сколько он молчал ДО этого сообщения
    hours = hours_since_previous_user_msg(user_id)
    if hours is None:
        return

    new_stage = _stage_from_hours(hours)
    old_stage = _get_stage(user_id)

    if new_stage == old_stage:
        return

    # Стадия изменилась
    _set_stage(user_id, new_stage)

    # Сдвиг mood только при СТАРТЕ новой стадии (new > old)
    if new_stage > old_stage and new_stage >= 2:
        _mood_shift_on_return(user_id, new_stage)

    # Если он вернулся (молчание короткое) — сбрасываем в 0
    if new_stage == 0 and old_stage > 0:
        _set_stage(user_id, 0)