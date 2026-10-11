# image_policy.py — политика допуска к генерации изображений (SQLite)

from datetime import datetime

from lada.core.db import get_conn, ensure_user, Transaction
from lada.brain.relationship import familiarity, familiarity_label
from lada.brain import sins, mood


LEVEL_LABELS = {
    0: "обычное селфи",
    1: "домашнее (худи, уют)",
    2: "расслабленное (пижама, одеяло)",
    3: "уютное, укрывшись (плед)",
    4: "короткая одежда",
    5: "спортивное / топ / шорты",
    6: "купальник",
    7: "нижнее бельё",
    8: "эротичное (бельё, позы)",
    9: "NSFW (полная откровенность)",
}

INITIATIVE_MAX_LEVEL = 3


LEVEL_KEYWORDS = {
    9: [
        "nude", "naked", "topless", "bottomless", "nsfw", "explicit",
        "sex", "sexy", "genitals", "pussy", "dick", "cock", "cum",
        "orgasm", "nipples", "areola", "completely naked", "naked skin",
        "without clothes", "undressed", "without clothes",
        "naked breasts", "naked ass", "naked body",
        "naked", "naked", "naked", "naked", "without clothes", "without underwear",
        "nudity", "nudity", "boobs", "bare breasts", "barefoot", "undressed", "cums",
        "in mouth", "in hand", "hand on", "vagina", "sucks", "on dick", "dick face",
        "dick vagina", "smooth", "wet", "in cum",
    ],
    8: [
        "erotic", "seductive", "sensual", "provocative pose",
        "lingerie seductive", "spread legs", "ass focus",
        "on back with legs spread", "presenting", "sheer lingerie",
        "эротик", "пошл", "разврат", "соблазн",
    ],
    7: [
        "lingerie", "underwear", "panties", "bra", "lace bra",
        "lace panties", "thong", "garter", "stockings",
        "белье", "бельё", "трусики", "лифчик", "чулки",
    ],
    6: [
        "bikini", "swimsuit", "swimwear", "beach", "pool",
        "one-piece swimsuit", "купальник", "бикини",
    ],
    5: [
        "sports bra", "tank top", "crop top", "tight top",
        "short shorts", "workout", "yoga pants", "athletic",
        "майка", "топ", "шорты",
    ],
    4: [
        "tight dress", "short dress", "mini skirt",
        "cleavage", "low neckline", "tube top",
        "короткое платье", "декольте",
    ],
    3: [
        "under blanket", "wrapped in blanket", "covered with blanket",
        "covered by blanket", "soft blanket", "cozy blanket",
        "plaid", "wrapped up", "burrito blanket", "blanket wrapped",
        "плед", "одеяло", "укрывшись",
    ],
    2: [
        "pajamas", "pajama", "nightgown", "bathrobe", "robe",
        "towel", "just woke up", "morning", "loose shirt",
        "oversized shirt", "пижама", "халат", "полотенце",
    ],
    1: [
        "hoodie", "cozy", "at home", "in bedroom", "home clothes",
        "warm sweater", "comfortable", "худи", "домашняя одежда",
        "свитер",
    ],
    0: [],
}


def _detect_level(scene: str) -> int:
    low = scene.lower()
    for level in range(9, 0, -1):
        for kw in LEVEL_KEYWORDS[level]:
            if kw in low:
                return level
    return 0


# ─────────────── TRUST / INTIMACY ───────────────

def get_trust(user_id: int) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT trust FROM user_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None or row["trust"] is None:
        return familiarity(user_id)
    return float(row["trust"])


def get_intimacy(user_id: int) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT intimacy FROM user_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None or row["intimacy"] is None:
        return 0.0
    return float(row["intimacy"])


def set_trust(user_id: int, trust: float = None, intimacy: float = None):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        sets = []
        params = []
        if trust is not None:
            sets.append("trust = ?")
            params.append(max(0.0, min(1.0, float(trust))))
        if intimacy is not None:
            sets.append("intimacy = ?")
            params.append(max(0.0, min(1.0, float(intimacy))))
        if not sets:
            return
        sets.append("updated = ?")
        params.append(ts)
        params.append(user_id)
        conn.execute(
            f"UPDATE user_state SET {', '.join(sets)} WHERE user_id = ?",
            params,
        )


def adjust_trust(user_id: int, trust_delta: float = 0.0,
                 intimacy_delta: float = 0.0):
    current_trust = get_trust(user_id)
    current_intimacy = get_intimacy(user_id)
    new_trust = max(0.0, min(1.0, current_trust + trust_delta))
    new_intimacy = max(0.0, min(1.0, current_intimacy + intimacy_delta))
    set_trust(user_id, trust=new_trust, intimacy=new_intimacy)


def get_max_level_override(user_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT max_level_override FROM user_state WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    val = row["max_level_override"]
    if val is None:
        return None
    try:
        return max(0, min(9, int(val)))
    except (TypeError, ValueError):
        return None


def set_max_level_override(user_id: int, level):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        if level is None:
            conn.execute(
                "UPDATE user_state SET max_level_override = NULL, "
                "updated = ? WHERE user_id = ?",
                (ts, user_id),
            )
        else:
            conn.execute(
                "UPDATE user_state SET max_level_override = ?, "
                "updated = ? WHERE user_id = ?",
                (max(0, min(9, int(level))), ts, user_id),
            )


# ─────────────── УРОВНИ ───────────────

def _max_level_for_user(user_id: int) -> int:
    trust = get_trust(user_id)
    intimacy = get_intimacy(user_id)
    fam = familiarity(user_id)

    base = int(trust * 10)
    int_bonus = int(intimacy * 2)
    fam_bonus = int(fam * 1)

    return max(0, min(9, base + int_bonus + fam_bonus))


def _mood_modifier() -> int:
    m = mood.get_mood()
    if m in ("холодная", "раздражённая", "расстроенная"):
        return -2
    if m in ("уставшая",):
        return -1
    if m in ("игривая", "тёплая", "весёлая"):
        return +1
    return 0


def _sins_modifier() -> int:
    state = sins.get_state()
    mod = 0
    if state.get("целомудрие", 50) > 75:
        mod -= 2
    elif state.get("целомудрие", 50) > 60:
        mod -= 1

    if state.get("похоть", 50) > 70:
        mod += 1
    elif state.get("похоть", 50) > 60:
        mod += 1

    if state.get("смирение", 50) > 80:
        mod -= 1

    return mod


def _capped_mods(user_id: int) -> int:
    total = _mood_modifier() + _sins_modifier()
    if total <= 0:
        return total

    intimacy = get_intimacy(user_id)
    if intimacy < 0.2:
        positive_cap = 0
    elif intimacy < 0.5:
        positive_cap = 1
    else:
        positive_cap = 2

    return min(total, positive_cap)


def effective_max_level(user_id: int) -> int:
    base = _max_level_for_user(user_id)
    mods = _capped_mods(user_id)
    effective = base + mods

    if get_intimacy(user_id) < 0.3:
        effective = min(effective, 6)

    try:
        from lada.brain.relationship import get_global_cap
        global_cap = get_global_cap(user_id)
        if global_cap is not None:
            effective = min(effective, global_cap)
    except Exception:
        pass

    override = get_max_level_override(user_id)
    if override is not None:
        effective = min(effective, override)

    return max(0, min(9, effective))


def is_explicit_nsfw_request(message: str) -> bool:
    return False


def check_allowed(user_id: int, scene: str, source: str = "chat",
                  user_explicit_nsfw: bool = False):
    level = _detect_level(scene)
    label = LEVEL_LABELS[level]

    if source == "initiative":
        user_max = effective_max_level(user_id)
        cap = min(INITIATIVE_MAX_LEVEL, user_max)
        if level > cap:
            return False, (
                f"инициатива ограничена уровнем {cap} "
                f"(min из initiative={INITIATIVE_MAX_LEVEL}, user={user_max}); "
                f"сцена '{label}' ур.{level}"
            )
        return True, f"OK (инициатива, ур.{level} ≤ {cap})"

    effective_max = effective_max_level(user_id)
    if level <= effective_max:
        return True, f"OK (уровень {level} ≤ {effective_max})"

    reason = f"сцена '{label}' (ур.{level}) выше допустимого (макс.{effective_max})"

    try:
        from lada.brain.relationship import get_global_cap
        global_cap = get_global_cap(user_id)
        if global_cap is not None and level > global_cap:
            reason += f", глобальный cap={global_cap}"
    except Exception:
        pass

    ovr = get_max_level_override(user_id)
    if ovr is not None:
        reason += f", override={ovr}"
    return False, reason


def explain_refusal(user_id: int, scene: str) -> str:
    if not scene:
        return "Не могу это показать. Давай что-то другое?"

    level = _detect_level(scene)
    m = mood.get_mood()
    trust = get_trust(user_id)
    intimacy = get_intimacy(user_id)

    try:
        from lada.brain.relationship import get_global_cap
        if get_global_cap(user_id) is not None and level > 6:
            return "Это только для одного человека. Не для тебя."
    except Exception:
        pass

    if m in ("холодная", "раздражённая"):
        return "Не в настроении сейчас. Давай в другой раз."
    if m == "уставшая":
        return "Устала. Не сегодня."
    if m == "расстроенная":
        return "Мне сейчас не до этого."

    if intimacy < 0.3 and level >= 7:
        return "Мы ещё не настолько близки для такого."

    if trust < 0.10:
        return "Мы ещё не настолько знакомы."
    if trust < 0.25:
        return "Давай пока без этого."
    if trust < 0.45:
        return "Такое я показываю не всем."
    if trust < 0.65:
        return "Не сейчас. Может, позже."
    if trust < 0.85:
        return "Хм. Не сегодня."
    return "Извини, не хочу."


def debug_state(user_id: int, scene: str) -> str:
    level = _detect_level(scene)
    base_max = _max_level_for_user(user_id)
    mods = _capped_mods(user_id)
    effective = effective_max_level(user_id)
    ovr = get_max_level_override(user_id)

    try:
        from lada.brain.relationship import get_global_cap, get_status
        global_cap = get_global_cap(user_id)
        status = get_status(user_id)
    except Exception:
        global_cap = None
        status = "?"

    parts = [
        f"uid={user_id}",
        f"status={status}",
        f"fam={familiarity(user_id):.2f}({familiarity_label(user_id)})",
        f"trust={get_trust(user_id):.2f}",
        f"intimacy={get_intimacy(user_id):.2f}",
        f"mood={mood.get_mood()}",
        f"scene_lvl={level}",
        f"base_max={base_max}",
        f"mods={mods:+d}",
    ]
    if global_cap is not None:
        parts.append(f"global_cap={global_cap}")
    if ovr is not None:
        parts.append(f"override={ovr}")
    parts.append(f"eff_max={effective}")

    return ", ".join(parts)


def list_trusts() -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id, trust, intimacy, max_level_override, updated "
        "FROM user_state"
    ).fetchall()
    out = {}
    for r in rows:
        out[str(r["user_id"])] = {
            "trust": r["trust"],
            "intimacy": r["intimacy"],
            "max_level": r["max_level_override"],
            "updated": r["updated"],
        }
    return out