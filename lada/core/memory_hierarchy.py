# lada/core/memory_hierarchy.py — раскладка памяти на три группы.
#
# Задача: превратить плоский список результатов search_emotional
# в структурированный блок для промпта:
#
#   === ПОСТОЯННЫЕ ФАКТЫ О НЁМ ===
#   === ВАЖНЫЕ ЭМОЦИИ / СОБЫТИЯ ===
#   === СВЕЖИЕ ТЕМЫ (последние дни) ===
#
# Модель видит разные категории — и по-разному к ним относится.
# Никакого нового поиска: работаем с тем, что вернул search_emotional.

from datetime import datetime, timedelta


# Свежесть: что считать «последними днями»
FRESH_DAYS = 3

# Порог яркой эмоции
EMOTION_VALENCE_THRESHOLD = 0.4


def _parse_ts(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except Exception:
        return None


def _is_fact(item: dict) -> bool:
    """Постоянный факт: observation, без яркой эмоции, старше свежести."""
    if item.get("category") != "observation":
        return False
    v = item.get("valence")
    if v is None:
        return True
    try:
        return abs(float(v)) < EMOTION_VALENCE_THRESHOLD
    except (TypeError, ValueError):
        return True


def _is_fresh(item: dict) -> bool:
    """Свежее: было за последние FRESH_DAYS дней."""
    ts = _parse_ts(item.get("timestamp", ""))
    if ts is None:
        return False
    return (datetime.now() - ts).days <= FRESH_DAYS


def _is_emotional(item: dict) -> bool:
    """Яркая эмоция или emotion-категория."""
    if item.get("category") == "emotion":
        return True
    v = item.get("valence")
    if v is None:
        return False
    try:
        return abs(float(v)) >= EMOTION_VALENCE_THRESHOLD
    except (TypeError, ValueError):
        return False


def _format_line(item: dict, show_date: bool = False) -> str:
    """Строка памяти с метками."""
    text = (item.get("text") or "").strip()
    v = item.get("valence")
    cat = item.get("category", "?")
    ts_raw = (item.get("timestamp") or "")[:10]

    v_str = ""
    if v is not None:
        try:
            v_str = f" (v={float(v):+.2f})"
        except (TypeError, ValueError):
            v_str = ""

    date_str = f" [{ts_raw}]" if show_date and ts_raw else ""

    return f"  · [{cat}]{v_str}{date_str} {text}"


def format_hierarchical(items: list) -> str:
    """
    Возвращает строку для промпта, разбитую на три блока.
    Если в каком-то блоке пусто — он не выводится.
    """
    if not items:
        return ""

    facts = []
    emotions = []
    fresh = []

    # Разбираем — каждая запись попадает ТОЛЬКО в одну группу.
    # Приоритет: эмоция > свежее > факт.
    for it in items:
        if _is_emotional(it):
            emotions.append(it)
        elif _is_fresh(it):
            fresh.append(it)
        elif _is_fact(it):
            facts.append(it)
        else:
            # Что-то среднее — в свежие по умолчанию
            fresh.append(it)

    # Ограничиваем размер каждого блока, чтобы промпт не раздувался
    facts = facts[:6]
    emotions = emotions[:5]
    fresh = fresh[:5]

    parts = []

    if facts:
        parts.append("=== ПОСТОЯННЫЕ ФАКТЫ О НЁМ ===")
        for it in facts:
            parts.append(_format_line(it))
        parts.append("(это устойчивое, не сиюминутное)")
        parts.append("")

    if emotions:
        parts.append("=== ВАЖНЫЕ ЭМОЦИИ / СОБЫТИЯ ===")
        for it in emotions:
            parts.append(_format_line(it))
        parts.append("(это окрашенное — учитывай валентность)")
        parts.append("")

    if fresh:
        parts.append("=== СВЕЖИЕ ТЕМЫ (последние дни) ===")
        for it in fresh:
            parts.append(_format_line(it, show_date=True))
        parts.append("")

    return "\n".join(parts).strip()