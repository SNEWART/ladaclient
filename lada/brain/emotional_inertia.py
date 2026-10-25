# lada/brain/emotional_inertia.py — C4: инерция эмоций.
#
# Идея: живой человек не переключается мгновенно. Если Лада
# была раздражена и через 5 минут ей говорят что-то тёплое —
# она не «сразу оттаивает», а держит осадок.
#
# Модуль перехватывает set_mood() из mood.py и решает,
# разрешить ли смену сейчас. Записывает историю смены.
#
# Что делает:
#   1. Логирует все смены mood в mood_history с valence.
#   2. Если последний негатив держится < INERTIA_HOLD_HOURS
#      и новый mood — резкий позитивный скачок → блокирует.
#   3. Если был резкий скачок недавно — блокирует обратный.
#
# Работает через check_and_apply(mood) — вызывается вместо
# mood.set_mood() в combined_eval.

import json
from datetime import datetime, timedelta

from config import (
    EMOTIONAL_INERTIA_ENABLED,
    INERTIA_VALENCE_THRESHOLD,
    INERTIA_HOLD_HOURS,
    INERTIA_NEGATIVE_BONUS,
)
from lada.core.db import get_conn, Transaction
from lada.brain import mood as mood_mod


_MOOD_VALENCE = {
    "весёлая": +1.0, "игривая": +0.8, "тёплая": +0.9,
    "вдохновлённая": +0.7, "спокойная": +0.2,
    "нейтральная": 0.0, "задумчивая": -0.1,
    "уставшая": -0.4, "холодная": -0.6,
    "расстроенная": -0.8, "раздражённая": -0.9,
}


def _valence(m: str) -> float:
    return _MOOD_VALENCE.get(m, 0.0)


def _log_change(new_mood: str):
    try:
        with Transaction() as conn:
            conn.execute(
                "INSERT INTO mood_history (mood, valence, ts) "
                "VALUES (?, ?, ?)",
                (new_mood, _valence(new_mood),
                 datetime.now().isoformat(timespec="seconds")),
            )
            # Чистим историю старше 7 дней
            cutoff = (datetime.now() - timedelta(days=7)
                      ).isoformat(timespec="seconds")
            conn.execute("DELETE FROM mood_history WHERE ts < ?", (cutoff,))
    except Exception as e:
        print(f"   ⚠️ mood_history: {e}")


def _recent_swings(hours: float = None) -> list:
    """Смены mood за последние N часов."""
    hours = hours or INERTIA_HOLD_HOURS
    cutoff = (datetime.now() - timedelta(hours=hours)
              ).isoformat(timespec="seconds")
    conn = get_conn()
    rows = conn.execute(
        "SELECT mood, valence, ts FROM mood_history "
        "WHERE ts >= ? ORDER BY id DESC",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _should_block(current: str, proposed: str) -> tuple:
    """
    Возвращает (block, reason).
    Блокируем если:
      — переход из негатива в позитив и негатив держится недолго,
      — переход из позитива в негатив был слишком резким.
    """
    cur_v = _valence(current)
    new_v = _valence(proposed)
    delta = new_v - cur_v

    if abs(delta) < INERTIA_VALENCE_THRESHOLD:
        return False, ""

    # Текущее mood держится долго? (нет свежих скачков в историю)
    history = _recent_swings(INERTIA_HOLD_HOURS)
    if not history:
        return False, ""

    # Ищем последнюю смену
    last = history[0]
    last_mood = last["mood"]
    last_v = last["valence"]
    try:
        last_dt = datetime.fromisoformat(last["ts"])
    except Exception:
        return False, ""

    hours_since = (datetime.now() - last_dt).total_seconds() / 3600

    # Случай: было негативно, предлагают резко позитивно,
    # но прошло мало времени → блокируем.
    if last_v < -0.3 and new_v > 0.5 and hours_since < INERTIA_HOLD_HOURS:
        bonus = INERTIA_NEGATIVE_BONUS if last_v < -0.6 else 1.0
        eff_hours = INERTIA_HOLD_HOURS * bonus
        if hours_since < eff_hours:
            return True, (
                f"была «{last_mood}» ({hours_since:.1f}ч назад), "
                f"не оттаивает мгновенно"
            )

    # Случай: было позитивно, предлагают резко негативно,
    # но прошло очень мало (30 мин) → блокируем (только что было хорошо).
    if last_v > 0.5 and new_v < -0.3 and hours_since < 0.5:
        return True, (
            f"только что было «{last_mood}» ({hours_since*60:.0f}м назад), "
            f"слишком резкий перепад"
        )

    return False, ""


def check_and_apply(proposed: str, source: str = "combined_eval") -> bool:
    """
    Заменяет mood.set_mood с инерцией.
    Возвращает True, если смена применена, False, если отложена.
    """
    current = mood_mod.get_mood()

    if not EMOTIONAL_INERTIA_ENABLED:
        mood_mod.set_mood(proposed)
        _log_change(proposed)
        return True

    if proposed == current:
        return True

    block, reason = _should_block(current, proposed)

    if block:
        print(f"   ⚖️ inertia: «{current}» → «{proposed}» "
              f"отложено ({reason})")
        # Смягчаем — двигаем в сторону, но не до конца.
        # Если предлагают резко позитив из негатива — идём в «нейтральную».
        softened = _soften(current, proposed)
        if softened != current:
            mood_mod.set_mood(softened)
            _log_change(softened)
            print(f"   ⚖️ inertia: смягчено до «{softened}»")
        return False

    mood_mod.set_mood(proposed)
    _log_change(proposed)
    return True


def _soften(current: str, proposed: str) -> str:
    """
    Вместо резкого скачка — идём на промежуточную ступень.
    Негатив → позитив: сначала «спокойная».
    Позитив → негатив: сначала «задумчивая».
    """
    cur_v = _valence(current)
    new_v = _valence(proposed)

    if cur_v < -0.3 and new_v > 0.5:
        return "спокойная"
    if cur_v > 0.5 and new_v < -0.3:
        return "задумчивая"
    return current


def regress_if_held(force: bool = False) -> bool:
    """
    Если негатив держится долго — отпускает постепенно.
    Вызывается из sins_regression_loop раз в час.
    """
    if not EMOTIONAL_INERTIA_ENABLED:
        return False

    current = mood_mod.get_mood()
    cur_v = _valence(current)
    if cur_v >= -0.3:
        return False

    # Негатив держится — проверим как долго
    history = _recent_swings(hours=24)
    if not history:
        return False

    # Ищем когда начался негатив
    neg_start = None
    for h in reversed(history):
        if h["valence"] < -0.3:
            try:
                neg_start = datetime.fromisoformat(h["ts"])
            except Exception:
                pass
            break

    if not neg_start:
        return False

    hours = (datetime.now() - neg_start).total_seconds() / 3600

    # Через 6 часов негатива — плавно к «задумчивой»,
    # через 12 — к «спокойной».
    if hours >= 12 and current != "спокойная":
        mood_mod.set_mood("спокойная")
        _log_change("спокойная")
        print(f"   ⚖️ inertia: негатив держался {hours:.0f}ч → «спокойная»")
        return True
    if hours >= 6 and current in ("раздражённая", "расстроенная",
                                    "холодная"):
        mood_mod.set_mood("задумчивая")
        _log_change("задумчивая")
        print(f"   ⚖️ inertia: негатив держался {hours:.0f}ч → «задумчивая»")
        return True

    return False


def as_context_block() -> str:
    """Короткое напоминание для промпта, если настроение «застряло»."""
    current = mood_mod.get_mood()
    cur_v = _valence(current)
    if cur_v >= -0.3:
        return ""

    history = _recent_swings(hours=24)
    if not history:
        return ""

    neg_start = None
    for h in reversed(history):
        if h["valence"] < -0.3:
            try:
                neg_start = datetime.fromisoformat(h["ts"])
            except Exception:
                pass
            break

    if not neg_start:
        return ""

    hours = (datetime.now() - neg_start).total_seconds() / 3600
    if hours < 1:
        return ""

    return (
        f"=== ТЫ НЕ В ДУХЕ УЖЕ {hours:.0f}ч ===\n"
        f"Это твоё состояние, оно не прошло. Не притворяйся, что всё "
        f"хорошо. Но и не устраивай сцену из-за этого — просто ты "
        f"сейчас такая.\n"
    )