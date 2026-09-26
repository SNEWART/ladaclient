# users_names.py — храним имена пользователей (username / first_name)

import json
from pathlib import Path
from datetime import datetime

FILE = Path("data") / "users_names.json"
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


def update(user_id: int, username: str = "", first_name: str = "",
           last_name: str = "", phone: str = ""):
    """Обновляет данные пользователя."""
    data = _load()
    key = str(user_id)
    entry = data.get(key, {})

    if username:
        entry["username"] = username.lstrip("@")
    if first_name:
        entry["first_name"] = first_name
    if last_name:
        entry["last_name"] = last_name
    if phone:
        entry["phone"] = phone

    entry["updated"] = datetime.now().isoformat(timespec="seconds")
    data[key] = entry
    _save(data)


def get(user_id: int) -> dict:
    return _load().get(str(user_id), {})


def display_name(user_id: int) -> str:
    """Человекочитаемое имя: @username > Имя Фамилия > ID."""
    data = get(user_id)
    if data.get("username"):
        return "@" + data["username"]

    parts = []
    if data.get("first_name"):
        parts.append(data["first_name"])
    if data.get("last_name"):
        parts.append(data["last_name"])
    if parts:
        return " ".join(parts)

    return f"ID {user_id}"


def all_names() -> dict:
    return _load()