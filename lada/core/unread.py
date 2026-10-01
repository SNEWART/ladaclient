# unread.py — учёт непрочитанных/неотвеченных сообщений.
#
# Логика: когда Лада решает промолчать ([SILENCE], ошибка, таймаут),
# сообщение не теряется — оно попадает в data/unread.json.
# Когда она отвечает — всё очищается.
#
# Фоновый return_loop периодически проверяет unread и даёт Ладе шанс
# вернуться к разговору.

import json
from datetime import datetime
from pathlib import Path

FILE = Path("data") / "unread.json"
FILE.parent.mkdir(exist_ok=True)

MAX_STORED_MESSAGES = 30


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


def append(user_id: int, text: str):
    """Запоминает сообщение как непрочитанное."""
    if not text or not text.strip():
        return
    data = _load()
    key = str(user_id)
    entry = data.get(key, {})
    pending = entry.get("pending", [])
    pending.append({
        "text": text.strip()[:500],
        "ts": datetime.now().isoformat(timespec="seconds"),
    })
    # не даём разрастись
    if len(pending) > MAX_STORED_MESSAGES:
        pending = pending[-MAX_STORED_MESSAGES:]
    entry["pending"] = pending
    entry["last_unread_at"] = pending[-1]["ts"]
    data[key] = entry
    _save(data)


def mark_read(user_id: int):
    """Очищает непрочитанные (когда Лада ответила)."""
    data = _load()
    key = str(user_id)
    if key in data:
        data[key].pop("pending", None)
        data[key].pop("last_unread_at", None)
        data[key].pop("last_return_attempt_at", None)
        if not data[key]:
            data.pop(key)
        _save(data)


def get_pending(user_id: int) -> list[dict]:
    """Список непрочитанных сообщений от пользователя."""
    return _load().get(str(user_id), {}).get("pending", []) or []


def count(user_id: int) -> int:
    return len(get_pending(user_id))


def list_users_with_pending() -> list[int]:
    """ID всех, у кого есть непрочитанные."""
    data = _load()
    out = []
    for k, v in data.items():
        if v.get("pending"):
            try:
                out.append(int(k))
            except ValueError:
                continue
    return out


def hours_since_last_unread(user_id: int) -> float:
    data = _load()
    entry = data.get(str(user_id), {})
    ts = entry.get("last_unread_at")
    if not ts:
        return 999.0
    try:
        dt = datetime.fromisoformat(ts)
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def hours_since_last_return_attempt(user_id: int) -> float:
    data = _load()
    entry = data.get(str(user_id), {})
    ts = entry.get("last_return_attempt_at")
    if not ts:
        return 999.0
    try:
        dt = datetime.fromisoformat(ts)
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def touch_return_attempt(user_id: int):
    data = _load()
    key = str(user_id)
    if key not in data:
        return
    data[key]["last_return_attempt_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)


def as_context_block(user_id: int) -> str:
    """
    Блок для промпта: «он написал тебе N раз, ты не ответила».
    Пустая строка если непрочитанных нет.
    """
    pending = get_pending(user_id)
    if not pending:
        return ""

    n = len(pending)
    lines = [
        f"=== ⏳ ОН ЖДЁТ ОТВЕТА ===",
        f"За последнее время он написал тебе {n} раз(а), "
        f"а ты не ответила. Его сообщения:",
    ]
    for m in pending[-10:]:
        ts = m.get("ts", "")[:16].replace("T", " ")
        lines.append(f"  [{ts}] {m['text'][:200]}")
    lines.append("")
    lines.append(
        "Ты можешь сейчас ответить — с учётом всего, что написано выше. "
        "Или коротко извиниться за паузу («сорри, залипла») и ответить по сути. "
        "Не пиши длинных извинений. Не делай вид, что ничего не было."
    )
    return "\n".join(lines)