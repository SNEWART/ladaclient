# memory.py — личная память (история диалога по пользователям)

import json
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data")
USERS_DIR = DATA_DIR / "users"
DATA_DIR.mkdir(exist_ok=True)
USERS_DIR.mkdir(exist_ok=True)


def _user_file(user_id: int) -> Path:
    return USERS_DIR / f"{user_id}.json"


def _load_user(user_id: int) -> list:
    f = _user_file(user_id)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_user(user_id: int, history: list):
    _user_file(user_id).write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_history(user_id: int, limit: int = None) -> list:
    history = _load_user(user_id)
    if limit:
        return history[-limit:]
    return history


def add_message(user_id: int, role: str, content: str, max_history: int = 20):
    history = _load_user(user_id)
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })
    _save_user(user_id, history[-max_history:])


def list_users() -> list:
    return [int(f.stem) for f in USERS_DIR.glob("*.json")]