# will.py — свобода воли Лады + история фото + детект границ + дождь-контроль.

import json
import re
from datetime import datetime
from pathlib import Path

from lada.brain import sins, mood
from lada.brain.relationship import familiarity_label, get_status

PHOTO_HISTORY_FILE = Path("data") / "photo_history.json"
PHOTO_HISTORY_FILE.parent.mkdir(exist_ok=True)

PHOTO_COOLDOWN_HINT = 15


# ─────────────────────────────────────────────
# ИСТОРИЯ ФОТО
# ─────────────────────────────────────────────
def _load_history() -> dict:
    if PHOTO_HISTORY_FILE.exists():
        try:
            return json.loads(PHOTO_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_history(data: dict):
    PHOTO_HISTORY_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_last_photo_msg_count(user_id: int) -> int:
    data = _load_history()
    return int(data.get(str(user_id), {}).get("last_photo_msg_count", 0))


def touch_photo_sent(user_id: int, msg_count: int, prompt: str):
    data = _load_history()
    key = str(user_id)
    entry = data.get(key, {})
    entry["last_photo_msg_count"] = int(msg_count)
    entry["total_photos"] = int(entry.get("total_photos", 0)) + 1
    recent = entry.get("recent_prompts", [])
    recent.append(prompt[:200])
    entry["recent_prompts"] = recent[-10:]
    data[key] = entry
    _save_history(data)


def messages_since_last_photo(user_id: int, current_count: int) -> int:
    last = get_last_photo_msg_count(user_id)
    if last <= 0:
        return 9999
    return max(0, current_count - last)


# ─────────────────────────────────────────────
# ДОЖДЬ-КОНТРОЛЬ
# ─────────────────────────────────────────────
# Считаем, сколько раз Лада упоминала дождь в последних N сообщениях.
# Если много — добавляем в state_brief блок «хватит про дождь».

RAIN_WORDS = ["дожд", "пасмурн", "сыро", "льёт", "льет", "моросит", "луж"]


def count_rain_mentions(history: list, only_assistant: bool = True) -> int:
    """Сколько раз в последних сообщениях встречалось упоминание дождя."""
    count = 0
    for m in history:
        if only_assistant and m.get("role") != "assistant":
            continue
        text = (m.get("content") or "").lower()
        if any(w in text for w in RAIN_WORDS):
            count += 1
    return count


def rain_signal(user_id: int, history: list) -> str | None:
    """
    Если Лада слишком часто говорила про дождь — возвращаем блок-предупреждение.
    Возвращает None, если всё нормально.
    """
    # берём последние 15 сообщений ассистента (примерно 7-8 обменов)
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


# ─────────────────────────────────────────────
# ДЕТЕКТ ГРАНИЦ
# ─────────────────────────────────────────────
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


def detect_boundary_request(message: str) -> str | None:
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


def boundary_signal(message: str) -> str | None:
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


# ─────────────────────────────────────────────
# СИГНАЛЫ ДЛЯ МОДЕЛИ
# ─────────────────────────────────────────────
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


def photo_signal(
    user_id: int,
    scene: str = "",
    source: str = "chat",
    msgs_since_last_photo: int = 9999,
    user_requested: bool = False,
) -> str:
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