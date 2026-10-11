# night_policy.py — правила общения Лады в ночное время.

from datetime import datetime

from config import (
    NIGHT_START_HOUR, NIGHT_END_HOUR,
    NIGHT_ASK_COOLDOWN_DAYS,
    NIGHT_ASK_WINDOW_START, NIGHT_ASK_WINDOW_END,
)
from lada.core.db import get_conn, ensure_user, Transaction


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_night_now() -> bool:
    h = datetime.now().hour
    if NIGHT_START_HOUR <= NIGHT_END_HOUR:
        return NIGHT_START_HOUR <= h < NIGHT_END_HOUR
    return h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR


def in_ask_window() -> bool:
    h = datetime.now().hour
    return NIGHT_ASK_WINDOW_START <= h < NIGHT_ASK_WINDOW_END


def get_night_ok(user_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT night_ok FROM user_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return row["night_ok"]


def get_night_pending(user_id: int) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT night_pending FROM user_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return False
    return bool(row["night_pending"])


def can_ask_about_night(user_id: int) -> bool:
    if get_night_ok(user_id) is not None:
        return False
    if get_night_pending(user_id):
        return False
    conn = get_conn()
    row = conn.execute(
        "SELECT night_rejected_at FROM user_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row and row["night_rejected_at"]:
        try:
            dt = datetime.fromisoformat(row["night_rejected_at"])
            days = (datetime.now() - dt).total_seconds() / 86400
            if days < NIGHT_ASK_COOLDOWN_DAYS:
                return False
        except Exception:
            pass
    return True


def should_ask_now(user_id: int) -> bool:
    if not in_ask_window():
        return False
    if not can_ask_about_night(user_id):
        return False
    try:
        from lada.brain import image_policy
        trust = image_policy.get_trust(user_id)
        if trust < 0.5:
            return False
    except Exception:
        pass
    return True


def record_night_ask(user_id: int):
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE user_state SET night_pending = 1, "
            "night_proposed_at = ?, updated = ? WHERE user_id = ?",
            (ts, ts, user_id),
        )


def accept_night(user_id: int):
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE user_state SET night_ok = 1, night_pending = NULL, "
            "updated = ? WHERE user_id = ?",
            (ts, user_id),
        )


def reject_night(user_id: int):
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE user_state SET night_ok = 0, night_pending = NULL, "
            "night_rejected_at = ?, updated = ? WHERE user_id = ?",
            (ts, ts, user_id),
        )


def revoke_night(user_id: int):
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE user_state SET night_ok = 0, night_pending = NULL, "
            "night_rejected_at = ?, updated = ? WHERE user_id = ?",
            (ts, ts, user_id),
        )


def detect_revoke_request(message: str) -> bool:
    if not message:
        return False
    low = message.lower()
    markers = [
        "не пиши ночью", "не пиши мне ночью",
        "не пиши в ночь", "не буди",
        "не пиши по ночам", "спать ночью",
        "не беспокой ночью", "не пиши ночами",
    ]
    return any(m in low for m in markers)