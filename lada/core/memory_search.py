# lada/core/memory_search.py — эмоционально-зависимый поиск по памяти.
#
# Берёт больше кандидатов из LanceDB, добавляет к cosine-скору «биас»
# от текущего состояния (mood, top_sins, trust, intimacy) и возвращает
# топ-k после ререйтинга.
#
# Записи без meta (старые) считаются нейтральными и не получают биаса,
# но и не отбрасываются.

from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.brain import mood as mood_mod
from lada.brain import sins as sins_mod
from lada.brain import image_policy


# Полярность текущего настроения. Используется как опорный знак:
# воспоминания того же знака получают плюс к скору.
MOOD_VALENCE = {
    "весёлая": +1.0, "игривая": +0.8, "тёплая": +0.9,
    "вдохновлённая": +0.7, "спокойная": +0.2,
    "нейтральная": 0.0, "задумчивая": -0.1,
    "уставшая": -0.4, "холодная": -0.6,
    "расстроенная": -0.8, "раздражённая": -0.9,
}


def _current_state(user_id):
    return {
        "mood": mood_mod.get_mood(),
        "sins": sins_mod.get_state(),
        "trust": image_policy.get_trust(user_id) if user_id else 0.0,
        "intimacy": image_policy.get_intimacy(user_id) if user_id else 0.0,
    }


def _bias(item: dict, state: dict) -> float:
    """Насколько эта запись уместна при текущем состоянии. Может быть <0."""
    meta = item.get("_meta") or {}
    valence = meta.get("valence")
    if valence is None:
        return 0.0
    try:
        valence = float(valence)
    except Exception:
        return 0.0

    cur_mood = state["mood"]
    cur_v = MOOD_VALENCE.get(cur_mood, 0.0)

    # 1. Совпадение знака текущего настроения и валентности записи.
    #    Совпадает → +, разное → −. Максимум ±0.15.
    sign_match = 1.0 if (valence >= 0) == (cur_v >= 0) else -1.0
    score = sign_match * abs(valence) * 0.15

    # 2. Гнев высокий: тянем отрицательные, отталкиваем тёплые.
    anger = state["sins"].get("гнев", 50)
    if anger > 65:
        if valence < 0:
            score += 0.10
        else:
            score -= 0.15

    # 3. Уныние высокое: тёплые воспоминания слегка отталкиваем.
    um = state["sins"].get("уныние", 50)
    if um > 70 and valence > 0.3:
        score -= 0.05

    # 4. Похоть высокая + игривая/тёплая: чуть приоритетнее игривые записи.
    lust = state["sins"].get("похоть", 50)
    mood_at = meta.get("mood_at_time") or ""
    if lust > 65 and mood_at in ("игривая", "тёплая"):
        score += 0.05

    # 5. Если сейчас близость выше, чем была тогда — приятное
    #    воспоминание чуть приоритетнее.
    trust_then = meta.get("trust_at_time")
    if trust_then is not None and valence > 0:
        try:
            trust_then = float(trust_then)
            if state["trust"] > trust_then + 0.15:
                score += 0.05
        except Exception:
            pass

    return score


def search_emotional(query: str, user_id: int | None = None,
                     k: int = 12, max_chars: int = 4000,
                     min_score: float | None = None) -> list:
    """
    Возвращает до k воспоминаний, отранжированных с учётом эмоций.

    Внутри:
      - берём k*4 кандидатов из LanceDB с пониженным порогом,
      - к каждому прибавляем эмоциональный биас,
      - сортируем и обрезаем по k и max_chars.
    """
    # Порог на входе в ререйтинг специально низкий, чтобы дать биасу
    # шанс «поднять» эмоционально близкие записи, которые формально
    # далеки по эмбеддингу.
    raw_min = min_score if min_score is not None else 0.25

    candidates = faiss_mem.search(
        query,
        k=max(k * 4, 20),
        user_id=user_id,
        min_score=raw_min,
        max_chars=max_chars * 4,
    )
    if not candidates:
        return []

    if user_id is not None:
        state = _current_state(user_id)
    else:
        state = {
            "mood": mood_mod.get_mood(),
            "sins": sins_mod.get_state(),
            "trust": 0.0,
            "intimacy": 0.0,
        }

    scored = []
    for c in candidates:
        try:
            base = float(c.get("_score", 0.5))
        except Exception:
            base = 0.5
        b = _bias(c, state)
        scored.append((base + b, c))

    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    total = 0
    for rank_score, item in scored:
        t = item.get("text", "")
        if total + len(t) > max_chars:
            continue
        item["_rank_score"] = round(rank_score, 4)
        out.append(item)
        total += len(t)
        if len(out) >= k:
            break

    return out


def debug_bias(query: str, user_id: int, k: int = 10) -> list:
    """Для отладки: показывает, как эмоции повлияли на ранжирование."""
    state = _current_state(user_id)
    candidates = faiss_mem.search(
        query, k=max(k * 4, 20), user_id=user_id,
        min_score=0.2, max_chars=20000,
    )
    rows = []
    for c in candidates:
        base = float(c.get("_score", 0.5))
        b = _bias(c, state)
        rows.append({
            "text": c.get("text", "")[:80],
            "category": c.get("category"),
            "valence": c.get("valence"),
            "base": round(base, 3),
            "bias": round(b, 3),
            "final": round(base + b, 3),
        })
    rows.sort(key=lambda x: x["final"], reverse=True)
    return rows[:k]