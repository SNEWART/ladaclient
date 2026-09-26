# image_policy.py — политика допуска к генерации изображений.
#
# Дополнения:
#   - get_global_cap: если у кого-то есть lover/husband, остальным cap 6
#   - adjust_trust: ручное изменение trust/intimacy из emotions.py

import json
from pathlib import Path
from datetime import datetime

from lada.brain.relationship import familiarity, familiarity_label
from lada.brain import sins, mood

TRUST_FILE = Path("data") / "user_trust.json"
TRUST_FILE.parent.mkdir(exist_ok=True)


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
        "sex", "sexual", "genitals", "pussy", "dick", "cock", "cum",
        "orgasm", "nipples", "areola", "completely nude", "bare skin",
        "no clothing", "undressed", "unclothed", "without clothes",
        "bare breasts", "bare chest", "bare ass", "naked body",
        "голая", "голый", "голое", "обнаж", "без одежды", "без белья",
        "нюд", "ню", "сиськи", "грудь голая", "голышом", "раздет",
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
def _load_trust_data() -> dict:
    if TRUST_FILE.exists():
        try:
            return json.loads(TRUST_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_trust_data(data: dict):
    TRUST_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_trust(user_id: int) -> float:
    data = _load_trust_data()
    entry = data.get(str(user_id), {})
    if "trust" in entry:
        return float(entry["trust"])
    return familiarity(user_id)


def get_intimacy(user_id: int) -> float:
    data = _load_trust_data()
    entry = data.get(str(user_id), {})
    return float(entry.get("intimacy", 0.0))


def set_trust(user_id: int, trust: float = None, intimacy: float = None):
    data = _load_trust_data()
    key = str(user_id)
    if key not in data:
        data[key] = {}
    if trust is not None:
        data[key]["trust"] = max(0.0, min(1.0, float(trust)))
    if intimacy is not None:
        data[key]["intimacy"] = max(0.0, min(1.0, float(intimacy)))
    data[key]["updated"] = datetime.now().isoformat(timespec="seconds")
    _save_trust_data(data)


def adjust_trust(user_id: int, trust_delta: float = 0.0, intimacy_delta: float = 0.0):
    """Прибавляет дельты к trust и intimacy, обрезает в [0,1]."""
    data = _load_trust_data()
    key = str(user_id)
    if key not in data:
        data[key] = {}

    current_trust = float(data[key].get("trust", familiarity(user_id)))
    current_intimacy = float(data[key].get("intimacy", 0.0))

    new_trust = max(0.0, min(1.0, current_trust + trust_delta))
    new_intimacy = max(0.0, min(1.0, current_intimacy + intimacy_delta))

    data[key]["trust"] = new_trust
    data[key]["intimacy"] = new_intimacy
    data[key]["updated"] = datetime.now().isoformat(timespec="seconds")
    _save_trust_data(data)


def get_max_level_override(user_id: int) -> int | None:
    data = _load_trust_data()
    entry = data.get(str(user_id), {})
    val = entry.get("max_level")
    if val is None:
        return None
    try:
        v = int(val)
    except (TypeError, ValueError):
        return None
    return max(0, min(9, v))


def set_max_level_override(user_id: int, level: int | None):
    data = _load_trust_data()
    key = str(user_id)
    if key not in data:
        data[key] = {}
    if level is None:
        data[key].pop("max_level", None)
    else:
        data[key]["max_level"] = max(0, min(9, int(level)))
    data[key]["updated"] = datetime.now().isoformat(timespec="seconds")
    _save_trust_data(data)


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

    # ─── Глобальный cap при наличии lover/husband ───
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


def check_allowed(
    user_id: int,
    scene: str,
    source: str = "chat",
    user_explicit_nsfw: bool = False,
) -> tuple:
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
            reason += f", глобальный cap={global_cap} (у кого-то есть lover/husband)"
    except Exception:
        pass

    ovr = get_max_level_override(user_id)
    if ovr is not None:
        reason += f", override={ovr}"
    return False, reason


def explain_refusal(user_id: int, scene: str) -> str:
    level = _detect_level(scene)
    m = mood.get_mood()
    trust = get_trust(user_id)
    intimacy = get_intimacy(user_id)

    # проверяем глобальный cap
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
    return _load_trust_data()