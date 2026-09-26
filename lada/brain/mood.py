# mood.py — поверхностное настроение

import json
from pathlib import Path
from datetime import datetime

FILE = Path("data") / "mood.json"
FILE.parent.mkdir(exist_ok=True)

VALID_MOODS = {
    "спокойная", "весёлая", "задумчивая", "раздражённая",
    "уставшая", "игривая", "холодная", "тёплая",
    "расстроенная", "вдохновлённая", "нейтральная",
}

DEFAULT = "нейтральная"


def _load() -> dict:
    if FILE.exists():
        try:
            return json.loads(FILE.read_text(encoding="utf-8"))
        except Exception:
            return {"mood": DEFAULT}
    return {"mood": DEFAULT}


def _save(data: dict):
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_mood() -> str:
    return _load().get("mood", DEFAULT)


def set_mood(mood: str):
    if mood not in VALID_MOODS:
        return
    data = _load()
    data["mood"] = mood
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    _save(data)


def describe() -> str:
    return f"Твоё текущее настроение: {get_mood()}."