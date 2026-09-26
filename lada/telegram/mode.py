# mode.py — хранит текущий режим (всем / только создателю)

from pathlib import Path
from config import REPLY_TO_ALL

MODE_FILE = Path("data") / "mode.txt"
MODE_FILE.parent.mkdir(exist_ok=True)


def get_mode() -> str:
    """Возвращает 'all' или 'owner'."""
    if MODE_FILE.exists():
        mode = MODE_FILE.read_text(encoding="utf-8").strip()
        if mode in ("all", "owner"):
            return mode
    return "all" if REPLY_TO_ALL else "owner"


def set_mode(mode: str):
    """Устанавливает режим. 'all' или 'owner'."""
    if mode not in ("all", "owner"):
        raise ValueError(f"Неизвестный режим: {mode}")
    MODE_FILE.write_text(mode, encoding="utf-8")


def should_reply(sender_id: int, owner_id: int) -> bool:
    """Проверяет, нужно ли отвечать этому пользователю."""
    mode = get_mode()
    if mode == "all":
        return True
    return sender_id == owner_id