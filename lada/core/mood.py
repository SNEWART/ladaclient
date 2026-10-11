# mood.py — поверхностное настроение (SQLite)

import json
from datetime import datetime

from lada.core.db import get_conn, Transaction


VALID_MOODS = {
    "спокойная", "весёлая", "задумчивая", "раздражённая",
    "уставшая", "игривая", "холодная", "тёплая",
    "расстроенная", "вдохновлённая", "нейтральная",
}

DEFAULT = "нейтральная"


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
    data["mood"] = mood
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    _write_state(data)


def describe() -> str:
    return f"Твоё текущее настроение: {get_mood()}."