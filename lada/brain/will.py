# will.py — свобода воли + история фото + детект границ + дождь-контроль
# (SQLite для photo_history)

import re
from datetime import datetime

from lada.core.db import get_conn, ensure_user, Transaction
from lada.brain import sins, mood
from lada.brain.relationship import familiarity_label, get_status


PHOTO_COOLDOWN_HINT = 15


# ─────────────── ИСТОРИЯ ФОТО ───────────────

def _read_photo_entry(user_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT last_photo_msg_count, total_photos, recent_prompts "
        "FROM photo_history WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return {"last_photo_msg_count": 0, "total_photos": 0,
                "recent_prompts": []}
    import json
    try:
        prompts = json.loads(row["recent_prompts"]) if row["recent_prompts"] else []
    except Exception:
        prompts = []
    return {
        "last_photo_msg_count": row["last_photo_msg_count"] or 0,
        "total_photos": row["total_photos"] or 0,
        "recent_prompts": prompts,
    }


def get_last_photo_msg_count(user_id: int) -> int:
    return int(_read_photo_entry(user_id)["last_photo_msg_count"])


def touch_photo_sent(user_id: int, msg_count: int, prompt: str):
    import json
    entry = _read_photo_entry(user_id)
    entry["last_photo_msg_count"] = int(msg_count)
    entry["total_photos"] = int(entry.get("total_photos", 0)) + 1
    recent = entry.get("recent_prompts", [])
    recent.append(prompt[:200])
    recent = recent[-10:]

    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO photo_history "
            "(user_id, last_photo_msg_count, total_photos, recent_prompts, updated) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "last_photo_msg_count = excluded.last_photo_msg_count, "
            "total_photos = excluded.total_photos, "
            "recent_prompts = excluded.recent_prompts, "
            "updated = excluded.updated",
            (user_id, entry["last_photo_msg_count"], entry["total_photos"],
             json.dumps(recent, ensure_ascii=False), ts),
        )


def messages_since_last_photo(user_id: int, current_count: int) -> int:
    last = get_last_photo_msg_count(user_id)
    if last <= 0:
        return 9999
    return max(0, current_count - last)


# ─────────────── ОПИСАНИЕ НАСТРОЕНИЯ ───────────────

def mood_descriptor_for_photo() -> str:
    m = mood.get_mood()
    mapping = {
        "спокойная": "расслаблена, готова делиться спокойными кадрами",
        "весёлая": "в хорошем настроении",
        "задумчивая": "погружена в себя",
        "раздражённая": "не в духе",
        "уставшая": "устала",
        "игривая": "игривая",
        "холодная": "отстранена, закрыта",
        "тёплая": "тёплая",
        "расстроенная": "расстроена",
        "вдохновлённая": "вдохновлена",
        "нейтральная": "нейтральна",
    }
    return mapping.get(m, "нейтральна")


# ─────────────── ДОЖДЬ-КОНТРОЛЬ ───────────────

RAIN_WORDS = ["дожд", "пасмурн", "сыро", "льёт", "льет", "моросит", "луж"]


def count_rain_mentions(history: list, only_assistant: bool = True) -> int:
    count = 0
    for m in history:
        if only_assistant and m.get("role") != "assistant":
            continue
        text = (m.get("content") or "").lower()
        if any(w in text for w in RAIN_WORDS):
            count += 1
    return count


def rain_signal(user_id: int):
    from lada.core.memory import get_history
    try:
        history = get_history(user_id, limit=30)
    except Exception:
        return None

    recent_assistant = [m for m in history if m.get("role") == "assistant"][-15:]
    rain_count = count_rain_mentions(recent_assistant)

    if rain_count >= 2:
        return (
            "⚠️ ТЫ СЛИШКОМ ЧАСТО ГОВОРИШЬ ПРО ДОЖДЬ.\n"
            f"За последние {len(recent_assistant)} твоих сообщений ты "
            f"упомянула дождь {rain_count} раз(а).\n"
            "Это выглядит навязчиво и скучно. Сейчас НЕ поднимай тему дождя. "
            "Не пиши про погоду вообще, если собеседник не спросит. "
            "Выбери другую тему: мысль, вопрос, чувство, наблюдение."
        )
    return None


# ─────────────── ДЕТЕКТ ГРАНИЦ ───────────────

BOUNDARY_MARKERS = [
    "не присылай", "не отправляй", "не показывай",
    "хватит фото", "хватит этих фото", "больше не присылай",
    "перестань присылать", "прекрати присылать",
    "не надо фото", "не хочу фото", "не хочу видеть",
    "убери это", "не присылай больше",
    "прекрати", "перестань", "хватит", "завязывай",
    "не делай так", "не делай этого", "не надо так",
    "я устал от этого", "меня это бесит", "бесит",
    "задолбал", "достал",
]

BOUNDARY_SOFT_MARKERS = [
    "пожалуйста, не", "очень прошу не", "я тебя прошу",
    "давай без", "без этого",
]


def detect_boundary_request(message: str):
    if not message:
        return None
    low = message.lower().strip()
    for m in BOUNDARY_MARKERS:
        if m in low:
            return m
    for m in BOUNDARY_SOFT_MARKERS:
        if m in low:
            return m
    return None


def boundary_signal(message: str):
    marker = detect_boundary_request(message)
    if not marker:
        return None
    return (
        f"⚠️ ПОЛЬЗОВАТЕЛЬ ПРЯМО ПОПРОСИЛ ГРАНИЦУ: «{marker}».\n"
        f"Это НЕ шутка. Это НЕ тест. Это НЕ повод пошутить в ответ.\n"
        f"Это реальная просьба, которую ты уважаешь СВОИМ решением. "
        f"Если просили не присылать фото — не присылай. "
        f"Если просили не делать X — не делай X. "
        f"Можешь ответить коротко: «Поняла. Ок.» или что-то в своём стиле. "
        f"Но не делай вид, что не услышала."
    )


# ─────────────── СИГНАЛЫ ───────────────

def response_signal(user_id: int, message: str) -> str:
    m = mood.get_mood()
    state = sins.get_state()
    fam_label = familiarity_label(user_id)
    text = (message or "").strip().lower()

    parts = [f"настроение: {m}", f"знакомство: {fam_label}"]

    if m in ("холодная", "раздражённая"):
        parts.append("сейчас тебе не хочется общаться")
    elif m == "расстроенная":
        parts.append("расстроена, мало сил на разговор")
    elif m == "уставшая":
        parts.append("устала, тянет к тишине")
    elif m in ("игривая", "тёплая", "весёлая"):
        parts.append("в настроении поболтать")

    if state.get("гнев", 50) > 70:
        parts.append("гнев высокий")
    if state.get("уныние", 50) > 80 and state.get("усердие", 50) < 30:
        parts.append("уныние сильное")

    if fam_label == "незнакомец" and len(text) < 6:
        parts.append("пустое сообщение от незнакомца")

    question_markers = ("?", "как ", "почему", "зачем", "что ", "расскажи", "помоги")
    if any(q in text for q in question_markers):
        parts.append("прямой вопрос, легко не проигнорировать")

    return "; ".join(parts)


def photo_signal(user_id: int, scene: str = "", source: str = "chat",
                 msgs_since_last_photo: int = 9999,
                 user_requested: bool = False) -> str:
    m = mood.get_mood()
    state = sins.get_state()
    status = get_status(user_id)

    parts = [f"настроение: {m}"]

    if m in ("холодная", "раздражённая"):
        parts.append("не хочется показывать себя")
    elif m == "расстроенная":
        parts.append("не до фото")
    elif m == "уставшая":
        parts.append("устала, фото даются тяжело")
    elif m == "игривая":
        parts.append("игривая")
    elif m == "тёплая":
        parts.append("тёплая")

    if state.get("гнев", 50) > 70:
        parts.append("гнев высокий")
    if state.get("целомудрие", 50) > 75:
        parts.append("целомудрие высокое")
    if state.get("похоть", 50) > 70:
        parts.append("похоть высокая")

    if status == "husband":
        parts.append("статус: муж")
    elif status == "lover":
        parts.append("статус: любовник")

    if user_requested:
        parts.append("пользователь просит фото — но это не приказ")

    if msgs_since_last_photo < PHOTO_COOLDOWN_HINT:
        parts.append(
            f"ты недавно отправляла фото ({msgs_since_last_photo} сообщ. назад) — "
            f"хорошо бы дать паузу"
        )
    else:
        parts.append(f"с последнего фото прошло {msgs_since_last_photo} сообщ.")

    return "; ".join(parts)


def has_spontaneous_urge() -> bool:
    import random
    m = mood.get_mood()
    state = sins.get_state()
    if m in ("холодная", "раздражённая", "уставшая"):
        return False
    if state.get("гнев", 50) > 70:
        return False
    base = 0.10
    if m in ("игривая", "вдохновлённая", "весёлая"):
        base += 0.10
    return random.random() < base