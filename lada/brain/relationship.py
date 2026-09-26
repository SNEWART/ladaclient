# relationship.py — знакомство, близость, статусы и ПРЕДЛОЖЕНИЕ ОТНОШЕНИЙ.
#
# Статусы: neutral → acquaintance → lover → husband.
# Статус ставится автоматически по intimacy ИЛИ вручную.
#
# ПРЕДЛОЖЕНИЕ:
# Когда intimacy достигает порога lover/husband, Лада может САМА предложить
# отношения. Это регистрируется в data/proposals.json. При отказе:
#   - intimacy -= PROPOSAL_REJECTION_INTIMACY_PENALTY (0.05)
#   - кулдаун PROPOSAL_REJECTION_COOLDOWN_DAYS (30)
#   - она помнит отказ, чувства остаются, но при следующем пороге попробует снова
# Всё это влияет на её эмоциональное состояние и видно в state_brief.

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from lada.core.memory import get_history

FIRST_SEEN_FILE = Path("data") / "first_seen.json"
STATUS_FILE = Path("data") / "relationships.json"
PROPOSALS_FILE = Path("data") / "proposals.json"
FIRST_SEEN_FILE.parent.mkdir(exist_ok=True)


PHASE1_DAYS = 30.0
PHASE1_LEVEL = 0.55
PHASE2_TAU = 180.0
MSGS_PER_DAY_EXPECTED = 2.0

STATUS_ORDER = ["neutral", "acquaintance", "lover", "husband"]
SPECIAL_STATUSES = {"lover", "husband"}


# ─────────────────────────────────────────────
# УТИЛИТЫ
# ─────────────────────────────────────────────
def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_json(path: Path, data: dict):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


# ─────────────────────────────────────────────
# ЗНАКОМСТВО
# ─────────────────────────────────────────────
def touch_first_seen(user_id: int):
    data = _load_json(FIRST_SEEN_FILE)
    key = str(user_id)
    if key not in data:
        data[key] = datetime.now().isoformat(timespec="seconds")
        _save_json(FIRST_SEEN_FILE, data)


def days_known(user_id: int) -> float:
    data = _load_json(FIRST_SEEN_FILE)
    ts = data.get(str(user_id))
    dt = _parse_iso(ts)
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


# ─────────────────────────────────────────────
# СТАТУСЫ
# ─────────────────────────────────────────────
def get_status(user_id: int) -> str:
    data = _load_json(STATUS_FILE)
    entry = data.get(str(user_id), {})
    return entry.get("status", "neutral")


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
    data = _load_json(STATUS_FILE)
    key = str(user_id)
    data[key] = {
        "status": status,
        "since": data.get(key, {}).get("since") or datetime.now().isoformat(timespec="seconds"),
        "updated": datetime.now().isoformat(timespec="seconds"),
        "note": note or data.get(key, {}).get("note", ""),
    }
    _save_json(STATUS_FILE, data)


def update_status_from_intimacy(user_id: int, intimacy: float):
    """
    НЕ повышает автоматически. Статус ставится только через принятие
    предложения (accept_proposal) или вручную. Эта функция оставлена
    для совместимости и вызывается, но не делает ничего.
    """
    return


def anyone_has_special_status() -> bool:
    data = _load_json(STATUS_FILE)
    for entry in data.values():
        if entry.get("status") in SPECIAL_STATUSES:
            return True
    return False


def special_holder_user_id() -> int | None:
    data = _load_json(STATUS_FILE)
    for uid_str, entry in data.items():
        if entry.get("status") in SPECIAL_STATUSES:
            try:
                return int(uid_str)
            except ValueError:
                continue
    return None


def get_global_cap(user_id: int) -> int | None:
    from config import GLOBAL_CAP_WHEN_TAKEN
    my_status = get_status(user_id)
    if my_status in SPECIAL_STATUSES:
        return None
    if anyone_has_special_status():
        return GLOBAL_CAP_WHEN_TAKEN
    return None


# ─────────────────────────────────────────────
# ПРЕДЛОЖЕНИЕ ОТНОШЕНИЙ
# ─────────────────────────────────────────────
def _load_proposals() -> dict:
    return _load_json(PROPOSALS_FILE)


def _save_proposals(data: dict):
    _save_json(PROPOSALS_FILE, data)


def get_proposal_state(user_id: int) -> dict:
    data = _load_proposals()
    return data.get(str(user_id), {})


def get_pending(user_id: int) -> str | None:
    """Активное предложение от Лады: 'lover' / 'husband' / None."""
    state = get_proposal_state(user_id)
    return state.get("pending")


def should_propose(user_id: int) -> str | None:
    """
    Пора ли Ладе предложить отношения. Возвращает 'lover' / 'husband' / None.
    Логика:
      - intimacy >= 0.90 → husband (если ещё не предлагала)
      - intimacy >= 0.70 → lover (если ещё не предлагала)
      - если недавно отказали — ждать кулдаун
      - если уже есть статус special — не предлагать
    """
    from config import (
        PROPOSAL_ENABLED, STATUS_INTIMACY_LOVER, STATUS_INTIMACY_HUSBAND,
        PROPOSAL_REJECTION_COOLDOWN_DAYS,
    )
    from lada.brain import image_policy

    if not PROPOSAL_ENABLED:
        return None

    # уже есть статус?
    if get_status(user_id) in SPECIAL_STATUSES:
        return None

    # активное предложение — не повторяем
    if get_pending(user_id):
        return None

    state = get_proposal_state(user_id)
    intimacy = image_policy.get_intimacy(user_id)

    # кулдаун после отказа?
    rej = state.get("last_rejection") or {}
    rej_at = _parse_iso(rej.get("at"))
    if rej_at:
        days_since = (datetime.now() - rej_at).total_seconds() / 86400.0
        if days_since < PROPOSAL_REJECTION_COOLDOWN_DAYS:
            return None

    # husband пробуем только если lover уже был предложен и отклонён/принят,
    # или если intimacy сразу выше 0.90
    if intimacy >= STATUS_INTIMACY_HUSBAND:
        last_h = _parse_iso(state.get("last_husband_proposal"))
        if not last_h or rej.get("which") != "husband":
            # предлагаем husband (один раз)
            if state.get("last_husband_proposal") and rej.get("which") != "husband":
                return None
            return "husband"

    if intimacy >= STATUS_INTIMACY_LOVER:
        last_l = _parse_iso(state.get("last_lover_proposal"))
        # если lover уже отклоняли — не предлагаем пока кулдаун, он выше
        if not last_l or rej.get("which") == "lover":
            # если отказ был по lover и кулдаун прошёл — можно попробовать снова
            if last_l and rej.get("which") == "lover":
                return "lover"
            if not last_l:
                return "lover"

    return None


def record_proposal(user_id: int, which: str):
    """Регистрирует, что Лада сделала предложение."""
    data = _load_proposals()
    key = str(user_id)
    entry = data.get(key, {})
    entry["pending"] = which
    entry["pending_since"] = datetime.now().isoformat(timespec="seconds")
    if which == "lover":
        entry["last_lover_proposal"] = datetime.now().isoformat(timespec="seconds")
    elif which == "husband":
        entry["last_husband_proposal"] = datetime.now().isoformat(timespec="seconds")
    data[key] = entry
    _save_proposals(data)


def accept_proposal(user_id: int) -> str | None:
    """Пользователь согласился. Ставим статус, убираем pending."""
    which = get_pending(user_id)
    if not which:
        return None

    data = _load_proposals()
    key = str(user_id)
    entry = data.get(key, {})
    entry.pop("pending", None)
    entry.pop("pending_since", None)
    entry["accepted"] = which
    entry["accepted_at"] = datetime.now().isoformat(timespec="seconds")
    data[key] = entry
    _save_proposals(data)

    set_status(user_id, which, note=f"принято предложение {which}")
    return which


def reject_proposal(user_id: int) -> str | None:
    """
    Пользователь отказал. Штраф к intimacy, sadness, кулдаун.
    Возвращает 'lover' / 'husband' — что было отвергнуто.
    """
    from config import (
        PROPOSAL_REJECTION_INTIMACY_PENALTY,
        PROPOSAL_REJECTION_SADNESS_DELTA,
    )
    from lada.brain import image_policy, sins

    which = get_pending(user_id)
    if not which:
        return None

    data = _load_proposals()
    key = str(user_id)
    entry = data.get(key, {})
    entry.pop("pending", None)
    entry.pop("pending_since", None)
    entry["last_rejection"] = {
        "which": which,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    data[key] = entry
    _save_proposals(data)

    # штраф к intimacy
    current = image_policy.get_intimacy(user_id)
    new_intimacy = max(0.0, current - PROPOSAL_REJECTION_INTIMACY_PENALTY)
    image_policy.set_trust(user_id, intimacy=new_intimacy)

    # штраф к шкалам — уныние вверх, усердие вниз
    try:
        sins.apply_deltas({
            "уныние": PROPOSAL_REJECTION_SADNESS_DELTA / 2,
            "усердие": -PROPOSAL_REJECTION_SADNESS_DELTA / 2,
        })
    except Exception:
        pass

    return which


def rejection_context(user_id: int) -> str | None:
    """
    Текстовый контекст о недавнем отказе для state_brief.
    """
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


# ─────────────────────────────────────────────
# КОНТЕКСТ ДЛЯ ПРОМПТА
# ─────────────────────────────────────────────
def emotion_impact_note(user_id: int, mood: str) -> str:
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