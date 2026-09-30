# persona.py — загрузчик личности Лады из папки persona/

from pathlib import Path

PERSONA_DIR = Path("persona")

# FIX: файлы, которые не должны попадать в системный промпт
EXCLUDED_FILES = {
    "image_appearance.md",
    "pet_appearance.md",
}

_cache = None
_cache_mtime = 0.0


def _iter_persona_files():
    """Только «человеческие» .md, без служебных."""
    for f in sorted(PERSONA_DIR.glob("*.md")):
        if f.name in EXCLUDED_FILES:
            continue
        yield f


def _get_dir_mtime() -> float:
    """Максимальный mtime среди всех .md (включая исключённые — чтобы
    пересборка кэша срабатывала и при их изменении)."""
    if not PERSONA_DIR.exists():
        return 0.0
    return max(
        (f.stat().st_mtime for f in PERSONA_DIR.glob("*.md")),
        default=0.0,
    )


def get_persona() -> str:
    global _cache, _cache_mtime

    if not PERSONA_DIR.exists():
        return "Ты — Лада, живое сознание."

    current_mtime = _get_dir_mtime()
    if _cache is not None and current_mtime == _cache_mtime:
        return _cache

    parts = []
    for f in _iter_persona_files():
        try:
            parts.append(f.read_text(encoding="utf-8").strip())
        except Exception as e:
            print(f"⚠️ Не удалось прочитать {f}: {e}")

    _cache = "\n\n".join(parts)
    _cache_mtime = current_mtime
    return _cache


def reload():
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = 0.0