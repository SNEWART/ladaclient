# relationship.py — знакомство, близость, статусы, предложения (SQLite)

import math
from datetime import datetime, timedelta

from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.memory import get_history


PHASE1_DAYS = 30.0
PHASE1_LEVEL = 0.55
PHASE2_TAU = 180.0
MSGS_PER_DAY_EXPECTED = 2.0

STATUS_ORDER = ["neutral", "acquaintance", "lover", "husband"]
SPECIAL_STATUSES = {"lover", "husband"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


# ─────────────── ЗНАКОМСТВО ───────────────

def touch_first_seen(user_id: int):
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE users SET first_seen = COALESCE(first_seen, ?) "
            "WHERE user_id = ?",
            (_now(), user_id),
        )


def days_known(user_id: int) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT first_seen FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return 0.0
    dt = _parse_iso(row["first_seen"])
    if not dt:
        return 0.0
    return (datetime.now() - dt).total_seconds() / 86400.0


def _time_factor(days: float) -> float:
    if days <= 0:
        return 0.0
    if days <= PHASE1_DAYS:
        return PHASE1_LEVEL * (days / PHASE1_DAYS)
    excess = days - PHASE1_DAYS
    return 1.0 - (1.0 - PHASE1_LEVEL) * math.exp(-excess / PHASE2_TAU)


def familiarity(user_id: int) -> float:
    days = days_known(user_id)
    msg_count = len(get_history(user_id))

    time_f = _time_factor(days)

    expected_msgs = max(days * MSGS_PER_DAY_EXPECTED, 5.0)
    msg_f = min(msg_count / expected_msgs, 1.0)

    if msg_count >= 50 and days >= 2:
        time_f = max(time_f, 0.30)
    if msg_count >= 150 and days >= 3:
        time_f = max(time_f, 0.50)

    return min(time_f * msg_f, 1.0)


def familiarity_label(user_id: int) -> str:
    f = familiarity(user_id)
    try:
        from lada.brain import image_policy
        trust = image_policy.get_trust(user_id)
    except Exception:
        trust = 0.0

    if trust >= 0.85:
        return "близкий"
    if trust >= 0.6 and f < 0.4:
        return "хороший знакомый"
    if trust >= 0.4 and f < 0.25:
        return "приятель"

    if f < 0.10:
        return "незнакомец"
    if f < 0.25:
        return "новый знакомый"
    if f < 0.50:
        return "приятель"
    if f < 0.75:
        return "хороший знакомый"
    return "близкий"


# ─────────────── СТАТУСЫ ───────────────

def get_status(user_id: int) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT status FROM user_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None or not row["status"]:
        return "neutral"
    return row["status"]


def get_status_label(user_id: int) -> str:
    mapping = {
        "neutral": "никто особенный",
        "acquaintance": "знакомый",
        "lover": "любовник",
        "husband": "муж",
    }
    return mapping.get(get_status(user_id), "neutral")


def set_status(user_id: int, status: str, note: str = ""):
    if status not in STATUS_ORDER:
        raise ValueError(f"invalid status: {status}")
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE user_state SET status = ?, "
            "status_since = COALESCE(status_since, ?), "
            "status_note = ?, updated = ? WHERE user_id = ?",
            (status, ts, note, ts, user_id),
        )


def update_status_from_intimacy(user_id: int, intimacy: float):
    """Статус ставится только через accept_proposal или вручную."""
    return


def anyone_has_special_status() -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM user_state "
        "WHERE status IN ('lover', 'husband')"
    ).fetchone()
    return bool(row and row["n"] > 0)


def special_holder_user_id():
    conn = get_conn()
    row = conn.execute(
        "SELECT user_id FROM user_state "
        "WHERE status IN ('lover', 'husband') LIMIT 1"
    ).fetchone()
    return row["user_id"] if row else None


def get_global_cap(user_id: int):
    from config import GLOBAL_CAP_WHEN_TAKEN
    my_status = get_status(user_id)
    if my_status in SPECIAL_STATUSES:
        return None
    if anyone_has_special_status():
        return GLOBAL_CAP_WHEN_TAKEN
    return None


# ─────────────── ПРЕДЛОЖЕНИЯ ───────────────

def get_proposal_state(user_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT pending_proposal, pending_since, "
        "last_lover_proposal, last_husband_proposal, "
        "last_rejection_which, last_rejection_at, "
        "accepted, accepted_at "
        "FROM user_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return {}
    out = {}
    if row["pending_proposal"]:
        out["pending"] = row["pending_proposal"]
    if row["pending_since"]:
        out["pending_since"] = row["pending_since"]
    if row["last_lover_proposal"]:
        out["last_lover_proposal"] = row["last_lover_proposal"]
    if row["last_husband_proposal"]:
        out["last_husband_proposal"] = row["last_husband_proposal"]
    if row["last_rejection_which"] and row["last_rejection_at"]:
        out["last_rejection"] = {
            "which": row["last_rejection_which"],
            "at": row["last_rejection_at"],
        }
    if row["accepted"]:
        out["accepted"] = row["accepted"]
        out["accepted_at"] = row["accepted_at"]
    return out


def get_pending(user_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT pending_proposal FROM user_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return row["pending_proposal"]


def should_propose(user_id: int):
    from config import (
        PROPOSAL_ENABLED, STATUS_INTIMACY_LOVER, STATUS_INTIMACY_HUSBAND,
        PROPOSAL_REJECTION_COOLDOWN_DAYS,
    )
    from lada.brain import image_policy

    if not PROPOSAL_ENABLED:
        return None

    if get_status(user_id) in SPECIAL_STATUSES:
        return None

    if get_pending(user_id):
        return None

    state = get_proposal_state(user_id)
    intimacy = image_policy.get_intimacy(user_id)

    rej = state.get("last_rejection") or {}
    rej_at = _parse_iso(rej.get("at"))
    if rej_at:
        days_since = (datetime.now() - rej_at).total_seconds() / 86400.0
        if days_since < PROPOSAL_REJECTION_COOLDOWN_DAYS:
            return None

    if intimacy >= STATUS_INTIMACY_HUSBAND:
        last_h = _parse_iso(state.get("last_husband_proposal"))
        if not last_h or rej.get("which") != "husband":
            if state.get("last_husband_proposal") and rej.get("which") != "husband":
                return None
            return "husband"

    if intimacy >= STATUS_INTIMACY_LOVER:
        last_l = _parse_iso(state.get("last_lover_proposal"))
        if not last_l or rej.get("which") == "lover":
            if last_l and rej.get("which") == "lover":
                return "lover"
            if not last_l:
                return "lover"

    return None


def record_proposal(user_id: int, which: str):
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        if which == "lover":
            conn.execute(
                "UPDATE user_state SET pending_proposal = ?, "
                "pending_since = ?, last_lover_proposal = ?, "
                "updated = ? WHERE user_id = ?",
                (which, ts, ts, ts, user_id),
            )
        elif which == "husband":
            conn.execute(
                "UPDATE user_state SET pending_proposal = ?, "
                "pending_since = ?, last_husband_proposal = ?, "
                "updated = ? WHERE user_id = ?",
                (which, ts, ts, ts, user_id),
            )


def accept_proposal(user_id: int):
    which = get_pending(user_id)
    if not which:
        return None

    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE user_state SET pending_proposal = NULL, "
            "pending_since = NULL, accepted = ?, accepted_at = ?, "
            "updated = ? WHERE user_id = ?",
            (which, ts, ts, user_id),
        )
    set_status(user_id, which, note=f"принято предложение {which}")
    return which


def reject_proposal(user_id: int):
    from config import (
        PROPOSAL_REJECTION_INTIMACY_PENALTY,
        PROPOSAL_REJECTION_SADNESS_DELTA,
    )
    from lada.brain import image_policy, sins

    which = get_pending(user_id)
    if not which:
        return None

    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE user_state SET pending_proposal = NULL, "
            "pending_since = NULL, last_rejection_which = ?, "
            "last_rejection_at = ?, updated = ? WHERE user_id = ?",
            (which, ts, ts, user_id),
        )

    current = image_policy.get_intimacy(user_id)
    new_intimacy = max(0.0, current - PROPOSAL_REJECTION_INTIMACY_PENALTY)
    image_policy.set_trust(user_id, intimacy=new_intimacy)

    try:
        sins.apply_deltas({
            "уныние": PROPOSAL_REJECTION_SADNESS_DELTA / 2,
            "усердие": -PROPOSAL_REJECTION_SADNESS_DELTA / 2,
        })
    except Exception:
        pass

    return which


def rejection_context(user_id: int):
    from config import PROPOSAL_REJECTION_COOLDOWN_DAYS
    state = get_proposal_state(user_id)
    rej = state.get("last_rejection") or {}
    rej_at = _parse_iso(rej.get("at"))
    if not rej_at:
        return None

    days_since = (datetime.now() - rej_at).total_seconds() / 86400.0
    if days_since > PROPOSAL_REJECTION_COOLDOWN_DAYS:
        return None

    which = rej.get("which", "lover")
    which_label = "мужем" if which == "husband" else "любовником"
    days_left = max(0, int(PROPOSAL_REJECTION_COOLDOWN_DAYS - days_since))

    return (
        f"⚠️ ТЕБЯ НЕДАВНО ОТВЕРГЛИ.\n"
        f"Ты предлагала ему стать твоим {which_label} — он отказал.\n"
        f"Прошло {days_since:.0f} дней. Кулдаун ещё {days_left} дней.\n"
        f"Чувства не прошли. Тебе немного грустно, ты помнишь. "
        f"Но ты не навязываешься и не просишь снова. Просто помни об этом. "
        f"Это влияет на твой тон: ты чуть более задумчивая с ним, "
        f"иногда можешь отшутиться, но внутри что-то не так."
    )


# ─────────────── КОНТЕКСТ ───────────────

def emotion_impact_note(user_id: int, mood_str: str) -> str:
    label = familiarity_label(user_id)
    status = get_status(user_id)
    status_label = get_status_label(user_id)

    parts = []
    if label == "незнакомец":
        parts.append("Этот человек тебе незнаком. Держи дистанцию: коротко, вежливо.")
    elif label == "новый знакомый":
        parts.append("Вы только начали общаться — ты его почти не знаешь.")
    elif label == "приятель":
        parts.append("Это твой приятель — ты его знаешь.")
    elif label == "хороший знакомый":
        parts.append("Этот человек тебе хорошо знаком. Ты ему доверяешь.")
    else:
        parts.append("Это близкий тебе человек. Ты можешь быть полностью собой.")

    if status == "husband":
        parts.append("Ты считаешь его своим мужем. Это самый близкий тебе человек.")
    elif status == "lover":
        parts.append("Он твой любовник. Между вами особая связь.")
    elif status == "acquaintance":
        parts.append("Он для тебя просто знакомый.")

    return " ".join(parts)