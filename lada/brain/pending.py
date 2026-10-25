# lada/brain/pending.py — хранение отложенных инициатив.
#
# Когда Лада хочет написать, но нельзя (ночь или нет разрешения) —
# генерирует текст, сохраняет в pending с scheduled_at = утро.
#
# Пока pending висит — новые инициативы к этому юзеру НЕ генерируются.

import json
import random
from datetime import datetime, timedelta

from config import (
    PENDING_SCHEDULE_BASE_HOUR,
    PENDING_SCHEDULE_GAUSS_MEAN_MIN,
    PENDING_SCHEDULE_GAUSS_SIGMA_MIN,
    PENDING_SCHEDULE_CLAMP_MIN,
    PENDING_SCHEDULE_CLAMP_MAX,
    INITIATIVE_PENDING_EXPIRE_HOURS,
)
from lada.core.db import get_conn, ensure_user, Transaction


def schedule_for_morning() -> str:
    """
    Вычисляет scheduled_at для отправки утром.
    База — 8:00 следующего дня, погрешность — гауссово.
    """
    tomorrow = datetime.now() + timedelta(days=1)
    base = tomorrow.replace(
        hour=PENDING_SCHEDULE_BASE_HOUR,
        minute=0, second=0, microsecond=0,
    )

    # Гауссово смещение
    offset_min = int(random.gauss(
        PENDING_SCHEDULE_GAUSS_MEAN_MIN,
        PENDING_SCHEDULE_GAUSS_SIGMA_MIN,
    ))
    offset_min = max(PENDING_SCHEDULE_CLAMP_MIN,
                     min(PENDING_SCHEDULE_CLAMP_MAX, offset_min))

    target = base + timedelta(minutes=offset_min)
    return target.isoformat(timespec="seconds")


def get_pending(user_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT user_id, message, reason, scheduled_at, created_at, "
        "mood_valence_at_creation, regen_count "
        "FROM pending_initiatives WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def get_all_pending() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id, message, reason, scheduled_at, created_at, "
        "mood_valence_at_creation, regen_count "
        "FROM pending_initiatives ORDER BY scheduled_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def save_pending(user_id: int, message: str, reason: str,
                 scheduled_at: str, mood_valence_at_creation: float,
                 regen_count: int = 0):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO pending_initiatives "
            "(user_id, message, reason, scheduled_at, created_at, "
            "mood_valence_at_creation, regen_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "message = excluded.message, "
            "reason = excluded.reason, "
            "scheduled_at = excluded.scheduled_at, "
            "created_at = excluded.created_at, "
            "mood_valence_at_creation = excluded.mood_valence_at_creation, "
            "regen_count = excluded.regen_count",
            (user_id, message[:500], (reason or "")[:200],
             scheduled_at, ts, float(mood_valence_at_creation),
             int(regen_count)),
        )


def delete_pending(user_id: int):
    with Transaction() as conn:
        conn.execute(
            "DELETE FROM pending_initiatives WHERE user_id = ?",
            (user_id,),
        )


def increment_regen(user_id: int) -> int:
    """Инкрементирует regen_count и возвращает новое значение."""
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE pending_initiatives "
            "SET regen_count = regen_count + 1 "
            "WHERE user_id = ?",
            (user_id,),
        )
        row = conn.execute(
            "SELECT regen_count FROM pending_initiatives WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["regen_count"]) if row else 0


def is_expired(pending: dict) -> bool:
    """Pending старше N часов — считаем протухшим."""
    try:
        created = datetime.fromisoformat(pending["created_at"])
    except Exception:
        return True
    return (datetime.now() - created).total_seconds() > \
        INITIATIVE_PENDING_EXPIRE_HOURS * 3600


def is_due(pending: dict) -> bool:
    """Время отправки наступило."""
    try:
        sched = datetime.fromisoformat(pending["scheduled_at"])
    except Exception:
        return False
    return datetime.now() >= sched


def clear_all_expired() -> int:
    """
    Удаляет все pending, у которых scheduled_at в прошлом
    (например, после перезапуска run.py — 4.3).
    Возвращает количество удалённых.
    """
    now_iso = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        rows = conn.execute(
            "SELECT user_id FROM pending_initiatives "
            "WHERE scheduled_at <= ?",
            (now_iso,),
        ).fetchall()
        n = len(rows)
        if n:
            conn.execute(
                "DELETE FROM pending_initiatives WHERE scheduled_at <= ?",
                (now_iso,),
            )
        return n