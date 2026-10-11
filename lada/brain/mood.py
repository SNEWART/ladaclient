# mood.py — поверхностное настроение (SQLite).
#
# FIX: добавлена авто-регрессия. Если настроение негативное и держится
# больше N часов без смены — оно уходит в нейтральное. Разрывает петлю
# «раздражена → молчит → эмоции не обновляются → остаётся раздражённой».

import json
from datetime import datetime

from lada.core.db import get_conn, Transaction


VALID_MOODS = {
    "спокойная", "весёлая", "задумчивая", "раздражённая",
    "уставшая", "игривая", "холодная", "тёплая",
    "расстроенная", "вдохновлённая", "нейтральная",
}

DEFAULT = "нейтральная"


# Сколько часов настроение держится, прежде чем регрессирует.
# У негативных — короче, чтобы выходила из цикла. У позитивных — дольше.
STALE_HOURS = {
    "уставшая": 2,
    "раздражённая": 3,
    "холодная": 3,
    "расстроенная": 4,
    "тёплая": 8,
    "весёлая": 8,
    "игривая": 8,
    "вдохновлённая": 8,
    "задумчивая": 10,
    "спокойная": 12,
    "нейтральная": 999,   # не трогаем
}


def _read_state() -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM lada_state WHERE key = 'mood'"
    ).fetchone()
    if not row or not row["value"]:
        return {"mood": DEFAULT}
    try:
        return json.loads(row["value"])
    except Exception:
        return {"mood": DEFAULT}


def _write_state(data: dict):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        conn.execute(
            "INSERT INTO lada_state (key, value, updated) VALUES ('mood', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated = excluded.updated",
            (json.dumps(data, ensure_ascii=False), ts),
        )


def get_mood() -> str:
    return _read_state().get("mood", DEFAULT)


def set_mood(mood: str):
    if mood not in VALID_MOODS:
        return
    data = _read_state()
    old = data.get("mood", DEFAULT)
    data["mood"] = mood
    now = datetime.now().isoformat(timespec="seconds")
    data["updated"] = now
    # Фиксируем момент смены настроения — только если оно реально меняется
    if old != mood:
        data["since"] = now
    elif "since" not in data:
        data["since"] = now
    _write_state(data)


def regress_if_stale() -> bool:
    """
    Если настроение держится дольше положенного — сдвигает его в нейтральное.
    Возвращает True, если что-то поменялось.
    """
    data = _read_state()
    m = data.get("mood", DEFAULT)
    if m == DEFAULT:
        return False

    since = data.get("since") or data.get("updated")
    if not since:
        return False

    try:
        dt = datetime.fromisoformat(since)
    except Exception:
        return False

    hours = (datetime.now() - dt).total_seconds() / 3600
    threshold = STALE_HOURS.get(m, 6)

    if hours >= threshold:
        set_mood(DEFAULT)
        print(f"   🎭 Настроение само вернулось к нейтральному "
              f"(было «{m}» {hours:.1f}ч)")
        return True

    return False


def describe() -> str:
    return f"Твоё текущее настроение: {get_mood()}."