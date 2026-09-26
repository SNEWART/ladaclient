# user_city.py — город каждого пользователя (для погоды)

import json
from pathlib import Path

FILE = Path("data") / "user_city.json"
FILE.parent.mkdir(exist_ok=True)


def _load() -> dict:
    if FILE.exists():
        try:
            return json.loads(FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict):
    FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_city(user_id: int) -> str | None:
    data = _load()
    city = data.get(str(user_id))
    if city:
        return str(city).strip()
    return None


def set_city(user_id: int, city: str | None):
    data = _load()
    key = str(user_id)
    if city:
        data[key] = city.strip()
    else:
        data.pop(key, None)
    _save(data)


def all_cities() -> dict:
    return _load()