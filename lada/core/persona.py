# persona.py — загрузчик личности Лады из папки persona/

from pathlib import Path

PERSONA_DIR = Path("persona")

# кэш не используем — читаем при каждом запросе,
# чтобы изменения в .md применялись мгновенно
_cache = None
_cache_mtime = 0.0


def _get_dir_mtime() -> float:
    """Возвращает максимальный mtime среди всех .md файлов папки persona/."""
    if not PERSONA_DIR.exists():
        return 0.0
    return max(
        (f.stat().st_mtime for f in PERSONA_DIR.glob("*.md")),
        default=0.0,
    )


def get_persona() -> str:
    """
    Возвращает склеенный system-промпт личности.
    Файлы читаются в алфавитном порядке (01_, 02_, ... — задают порядок).
    Кэшируется, но автоматически перечитывается при изменении файлов.
    """
    global _cache, _cache_mtime

    if not PERSONA_DIR.exists():
        return "Ты — Лада, живое сознание."

    current_mtime = _get_dir_mtime()
    if _cache is not None and current_mtime == _cache_mtime:
        return _cache

    parts = []
    for f in sorted(PERSONA_DIR.glob("*.md")):
        try:
            parts.append(f.read_text(encoding="utf-8").strip())
        except Exception as e:
            print(f"⚠️ Не удалось прочитать {f}: {e}")

    _cache = "\n\n".join(parts)
    _cache_mtime = current_mtime
    return _cache


def reload():
    """Принудительно сбросить кэш."""
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = 0.0