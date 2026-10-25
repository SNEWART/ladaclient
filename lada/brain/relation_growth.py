# lada/brain/relation_growth.py — медленный рост trust и intimacy.
#
# Идея: это шкалы доверия и близости. Живая девушка не влюбляется
# за день. Рост зависит от:
#   1. Знакомства (familiarity) — тот же модуль, что для отношений.
#      На первых днях рост почти нулевой.
#   2. Уровня (tier) — чем выше, тем медленнее. На trust > 0.85
#      рост почти останавливается без больших событий.
#   3. Дневного лимита — нельзя накопить больше X за сутки.
#   4. Intimacy растёт только если trust уже выше порога.
#
# Падение (негатив) — не затормаживается. Потерять доверие можно
# быстро, это реалистично.

import json
from datetime import datetime, timedelta

from config import (
    RELATION_GROWTH_ENABLED,
    TRUST_DAILY_CAP,
    INTIMACY_DAILY_CAP,
    INTIMACY_TRUST_GATE,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.brain import image_policy
from lada.brain.relationship import familiarity


# ─── Кривые роста ───
#
# Пары (нижняя_граница, верхняя_граница, множитель).
# Множитель применяется к предложенной дельте.
# На высоких уровнях он становится близок к нулю.

_TRUST_TIERS = [
    (0.00, 0.20, 1.00),   # 0.00-0.20 — можно расти
    (0.20, 0.40, 0.55),   # 0.20-0.40 — медленнее
    (0.40, 0.60, 0.30),   # 0.40-0.60 — в 3 раза медленнее
    (0.60, 0.75, 0.15),   # 0.60-0.75 — в 7 раз медленнее
    (0.75, 0.85, 0.07),   # 0.75-0.85 — в 14 раз
    (0.85, 0.95, 0.03),   # 0.85-0.95 — в 30 раз
    (0.95, 1.01, 0.01),   # 0.95-1.00 — только через большие события
]

_INTIMACY_TIERS = [
    (0.00, 0.20, 0.60),
    (0.20, 0.40, 0.35),
    (0.40, 0.60, 0.18),
    (0.60, 0.75, 0.08),
    (0.75, 0.85, 0.03),
    (0.85, 1.01, 0.01),
]


def _pick_multiplier(value: float, tiers: list) -> float:
    for lo, hi, m in tiers:
        if lo <= value < hi:
            return m
    return 0.01


def _familiarity_factor(user_id: int) -> float:
    """Множитель по времени знакомства. Растёт вместе с familiarity."""
    fam = familiarity(user_id)
    if fam < 0.05:
        return 0.05
    if fam < 0.15:
        return 0.15
    if fam < 0.30:
        return 0.35
    if fam < 0.50:
        return 0.60
    if fam < 0.75:
        return 0.85
    return 1.00


def _daily_key(user_id: int) -> str:
    return f"relation_daily_{user_id}"


def _load_daily(user_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT value, updated FROM lada_state WHERE key = ?",
        (_daily_key(user_id),),
    ).fetchone()
    default = {
        "trust": 0.0,
        "intimacy": 0.0,
        "since": datetime.now().isoformat(timespec="seconds"),
    }
    if not row or not row["value"]:
        return default
    try:
        data = json.loads(row["value"])
    except Exception:
        return default

    # Сброс раз в 24 часа
    try:
        since = datetime.fromisoformat(data.get("since", ""))
    except Exception:
        since = datetime.now()
    if (datetime.now() - since).total_seconds() >= 86400:
        return default
    return data


def _save_daily(user_id: int, data: dict):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO lada_state (key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated = excluded.updated",
            (_daily_key(user_id), json.dumps(data, ensure_ascii=False), ts),
        )


# ─── Публичное API ───

def apply_trust(user_id: int, proposed_delta: float) -> tuple:
    """
    Применяет delta к trust с учётом tier / familiarity / дневного
    лимита. Возвращает (actual_delta, reason).
    """
    if not RELATION_GROWTH_ENABLED:
        return proposed_delta, "disabled"

    current = image_policy.get_trust(user_id)
    delta = proposed_delta

    # Негатив не замедляем
    if delta < 0:
        new_val = max(0.0, current + delta)
        image_policy.set_trust(user_id, trust=new_val)
        return (new_val - current), "negative"

    # Позитив — применяем барьеры
    mult_tier = _pick_multiplier(current, _TRUST_TIERS)
    mult_fam = _familiarity_factor(user_id)
    delta *= mult_tier * mult_fam

    # Дневной лимит
    daily = _load_daily(user_id)
    used = float(daily.get("trust", 0.0))
    remaining = max(0.0, TRUST_DAILY_CAP - used)
    if delta > remaining:
        delta = remaining

    if delta <= 0.0001:
        return 0.0, f"лимит (tier×{mult_tier} fam×{mult_fam})"

    new_val = min(1.0, current + delta)
    image_policy.set_trust(user_id, trust=new_val)

    daily["trust"] = used + delta
    _save_daily(user_id, daily)

    return (new_val - current), f"tier×{mult_tier} fam×{mult_fam}"


def apply_intimacy(user_id: int, proposed_delta: float) -> tuple:
    """
    Применяет delta к intimacy. Гейт: если trust < INTIMACY_TRUST_GATE,
    позитив не применяется вообще. Возвращает (actual_delta, reason).
    """
    if not RELATION_GROWTH_ENABLED:
        return proposed_delta, "disabled"

    current = image_policy.get_intimacy(user_id)
    trust_now = image_policy.get_trust(user_id)
    delta = proposed_delta

    # Негатив не замедляем
    if delta < 0:
        new_val = max(0.0, current + delta)
        image_policy.set_trust(user_id, intimacy=new_val)
        return (new_val - current), "negative"

    # Гейт по trust
    if trust_now < INTIMACY_TRUST_GATE:
        return 0.0, f"trust<{INTIMACY_TRUST_GATE} (сейчас {trust_now:.2f})"

    mult_tier = _pick_multiplier(current, _INTIMACY_TIERS)
    mult_fam = _familiarity_factor(user_id)
    delta *= mult_tier * mult_fam

    daily = _load_daily(user_id)
    used = float(daily.get("intimacy", 0.0))
    remaining = max(0.0, INTIMACY_DAILY_CAP - used)
    if delta > remaining:
        delta = remaining

    if delta <= 0.0001:
        return 0.0, f"лимит (tier×{mult_tier} fam×{mult_fam})"

    new_val = min(1.0, current + delta)
    image_policy.set_trust(user_id, intimacy=new_val)

    daily["intimacy"] = used + delta
    _save_daily(user_id, daily)

    return (new_val - current), f"tier×{mult_tier} fam×{mult_fam}"


def debug(user_id: int) -> str:
    """Строка для отладки."""
    cur_t = image_policy.get_trust(user_id)
    cur_i = image_policy.get_intimacy(user_id)
    fam = familiarity(user_id)
    daily = _load_daily(user_id)
    return (
        f"trust={cur_t:.3f} (tier mult "
        f"{_pick_multiplier(cur_t, _TRUST_TIERS)}) "
        f"intimacy={cur_i:.3f} (tier mult "
        f"{_pick_multiplier(cur_i, _INTIMACY_TIERS)}) "
        f"familiarity={fam:.3f} (fam mult {_familiarity_factor(user_id)}) "
        f"daily trust {daily.get('trust', 0):.3f}/{TRUST_DAILY_CAP} "
        f"intimacy {daily.get('intimacy', 0):.3f}/{INTIMACY_DAILY_CAP}"
    )


def reset_relation(user_id: int, trust: float = 0.15,
                    intimacy: float = 0.0):
    """Сброс на стартовое состояние. Для миграции после раздутия."""
    image_policy.set_trust(user_id, trust=trust, intimacy=intimacy)
    # Чистим дневной счётчик
    with Transaction() as conn:
        conn.execute(
            "DELETE FROM lada_state WHERE key = ?",
            (_daily_key(user_id),),
        )