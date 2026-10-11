# activity.py — «что Лада сейчас делает».
#
# У живого человека в мессенджере всегда есть фоновое занятие:
# пьёт кофе, валяется с кошкой, смотрит сериал. Оно влияет на ответы
# и создаёт ощущение, что за чатом есть жизнь.
#
# Хранится в lada_state под ключом 'activity'.
# Формат value: {"text": "...", "period": "утро", "since": "ISO"}.
#
# Заменяется раз в 1-3 часа случайно. Выбор зависит от времени суток.
# Без LLM — только пул и рандом. Никакого VRAM.

import json
import random
from datetime import datetime

from lada.core.db import get_conn, Transaction


STATE_KEY = "activity"


# ─────────────── ПУЛ ЗАНЯТИЙ ───────────────
# Формулировки от первого лица, в настоящем времени.
# Чем живее — тем лучше.

POOL = {
    "утро": [
        "только проснулась, ещё не отошла",
        "пью кофе на кухне",
        "варю вторую чашку, первая не помогла",
        "мою голову, волосы мокрые",
        "собираюсь на работу, копаюсь в шкафу",
        "завтракаю, лень что-то серьёзное готовить",
        "сижу с Мусей на подоконнике, смотрю во двор",
        "пытаюсь вспомнить, что снилось",
        "листаю телефон, никак не встану",
        "стою в очереди за кофе",
    ],
    "день": [
        "работаю, отвечаю на письма",
        "залипла в отчёте",
        "делаю перерыв, пью чай",
        "гуляю по району, решила размяться",
        "обедаю, что-то простенькое",
        "сижу в кафе, работаю с ноутбука",
        "разбираю почту, ничего интересного",
        "зашла в магазин, стою у полки с кофе",
        "сижу на встрече, скучно, отвлекаюсь",
        "закрыла задачу, сижу довольная",
    ],
    "вечер": [
        "валяюсь с Мусей на диване",
        "смотрю сериал, залипла",
        "готовлю ужин, что-то простое",
        "сижу с книгой, никак не дочитаю",
        "пью вино и туплю в потолок",
        "вышла прогуляться, вечером хорошо",
        "слушаю музыку, ничего не делаю",
        "приняла ванну, расслабляюсь",
        "разговариваю с Мусей, она меня игнорирует",
        "переписываюсь с подругой",
    ],
    "ночь": [
        "не спится, лежу с телефоном",
        "смотрю ещё одну серию, потом спать",
        "думаю о разном, залипла",
        "Муся пришла на подушку, не двигаюсь",
        "пью чай, уже поздно",
        "читаю в кровати, глаза слипаются",
        "уже почти сплю, но не могу оторваться от телефона",
    ],
}


_PERIOD_BY_HOUR = {
    # hour: period (для диапазона берём по верхней границе)
    # 06:00-10:59 → утро, 11:00-17:59 → день, 18:00-22:59 → вечер, 23:00-05:59 → ночь
}


def _period_now() -> str:
    h = datetime.now().hour
    if 6 <= h < 11:
        return "утро"
    if 11 <= h < 18:
        return "день"
    if 18 <= h < 23:
        return "вечер"
    return "ночь"


def _load_state() -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM lada_state WHERE key = ?",
        (STATE_KEY,),
    ).fetchone()
    if not row or not row["value"]:
        return {}
    try:
        return json.loads(row["value"])
    except Exception:
        return {}


def _save_state(data: dict):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        conn.execute(
            "INSERT INTO lada_state (key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated = excluded.updated",
            (STATE_KEY, json.dumps(data, ensure_ascii=False), ts),
        )


# ─────────────── ПУБЛИЧНОЕ API ───────────────

def get_current() -> dict:
    """Возвращает {"text": ..., "period": ..., "since": ...} или {}."""
    return _load_state()


def get_current_text() -> str:
    """Только текст занятия, или пустая строка."""
    return _load_state().get("text", "")


def set_current(text: str):
    """Принудительно устанавливает занятие. Для тестов и /activity."""
    if not text or not text.strip():
        return
    _save_state({
        "text": text.strip(),
        "period": _period_now(),
        "since": datetime.now().isoformat(timespec="seconds"),
    })


def pick_new() -> str:
    """Выбирает случайное занятие из пула текущего периода."""
    period = _period_now()
    options = POOL.get(period) or POOL["день"]

    current = _load_state().get("text", "")
    # Не повторяем то же самое
    choices = [t for t in options if t != current]
    if not choices:
        choices = options

    return random.choice(choices)


def rotate_if_needed() -> str:
    """
    Меняет занятие, если оно устарело (period сменился или прошло 2+ часа).
    Возвращает актуальный текст.
    """
    state = _load_state()
    current_text = state.get("text", "")
    current_period = state.get("period", "")
    since = state.get("since", "")

    now_period = _period_now()

    # если ничего нет — ставим сразу
    if not current_text:
        new_text = pick_new()
        set_current(new_text)
        return new_text

    # если период сменился — меняем
    if current_period != now_period:
        new_text = pick_new()
        set_current(new_text)
        return new_text

    # если прошло 2+ часа — меняем
    if since:
        try:
            dt = datetime.fromisoformat(since)
            hours = (datetime.now() - dt).total_seconds() / 3600
            if hours >= 2:
                new_text = pick_new()
                set_current(new_text)
                return new_text
        except Exception:
            pass

    return current_text


# ─────────────── ФОНОВЫЙ ЦИКЛ ───────────────

async def activity_loop():
    """
    Раз в N минут проверяет, не пора ли сменить занятие.
    Проверка раз в 10 минут — сама логика меняет раз в 2 часа.
    Никаких LLM-вызовов, только выбор из пула.
    """
    import asyncio

    print("   🎭 activity_loop запущен (обновление раз в 2 ч)")

    # Ставим занятие сразу при старте
    rotate_if_needed()
    current = get_current_text()
    print(f"   🎭 Сейчас Лада: {current}")

    while True:
        await asyncio.sleep(600)  # 10 минут
        try:
            before = get_current_text()
            after = rotate_if_needed()
            if after != before:
                print(f"   🎭 Новое занятие: {after}")
        except Exception as e:
            print(f"   ⚠️ activity_loop: {e}")