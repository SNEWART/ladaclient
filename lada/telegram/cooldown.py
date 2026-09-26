# cooldown.py — защита от спама: не отвечать одному пользователю слишком часто

import json
from pathlib import Path
from datetime import datetime

FILE = Path("data") / "cooldown.json"
FILE.parent.mkdir(exist_ok=True)


def _load() -> dict:
    if FILE.exists():
        try:
            return json.loads(FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(d: dict):
    FILE.write_text(
        json.dumps(d, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def is_allowed(user_id: int, cooldown_seconds: int) -> tuple[bool, int]:
    """
    Возвращает (разрешено_ли_ответить, сколько_секунд_ждать).
    """
    if cooldown_seconds <= 0:
        return True, 0

    data = _load()
    last = data.get(str(user_id))
    if not last:
        return True, 0

    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True, 0

    elapsed = (datetime.now() - last_dt).total_seconds()
    if elapsed < cooldown_seconds:
        return False, int(cooldown_seconds - elapsed)
    return True, 0


def touch(user_id: int):
    """Отмечает момент ответа для пользователя."""
    data = _load()
    data[str(user_id)] = datetime.now().isoformat(timespec="seconds")
    _save(data)