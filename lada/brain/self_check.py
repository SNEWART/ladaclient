# lada/brain/self_check.py — условный второй проход.
#
# Задача: ловить случаи, когда основная модель «поплыла», и просить
# её переписать ОДИН раз. Вызывается только при срабатывании триггера,
# чтобы не удваивать задержку в обычных диалогах.

import asyncio
import re
from datetime import datetime

import ollama

from config import (
    SELF_CHECK_ENABLED,
    SELF_CHECK_MAX_REPLY_LEN,
    MODEL,
)
from lada.brain import mood as mood_mod


# ─────────────── триггеры ───────────────

_MOOD_VALENCE = {
    "весёлая": +1.0, "игривая": +0.8, "тёплая": +0.9,
    "вдохновлённая": +0.7, "спокойная": +0.2,
    "нейтральная": 0.0, "задумчивая": -0.1,
    "уставшая": -0.4, "холодная": -0.6,
    "расстроенная": -0.8, "раздражённая": -0.9,
}


def _valence(m: str) -> float:
    return _MOOD_VALENCE.get(m, 0.0)


def _is_suspicious(reply: str, user_msg: str,
                    had_regex_strip: bool,
                    had_fake_strip: bool) -> tuple:
    """
    Возвращает (suspicious: bool, reason: str).
    """
    if not reply:
        return False, ""

    r = reply.strip()

    # 1. Слишком длинный ответ (монолог)
    if len(r) > SELF_CHECK_MAX_REPLY_LEN:
        return True, f"длинный ответ ({len(r)} симв.)"

    # 2. Регексы что-то резали — модель уходила не туда
    if had_regex_strip:
        return True, "сработал regex (ремарки/действия)"

    if had_fake_strip:
        return True, "сработал regex (псевдо-пометки)"

    # 3. Заглушка вместо ответа
    if r.lower() in ("сейчас, секунду.", "сейчас, секунду", "…", "..."):
        return True, "заглушка"

    # 4. Вопросительный ответ на вопрос (эхо)
    if user_msg:
        u = user_msg.strip()
        if u.endswith("?") and r.endswith("?"):
            # Оба вопроса — подозрительно (модель ушла в echo)
            return True, "ответ-вопрос на вопрос"

    # 5. Резкий контраст эмоции: настроение негативное, а ответ бодрый
    m = mood_mod.get_mood()
    cur_v = _valence(m)
    if cur_v < -0.4:
        # Ищем слишком бодрые признаки
        bright = ("!", "😊", "😄", "😁", "рад", "класс", "супер", "здорово",
                  "отлично", "прекрасно", "ура", "😹", "🥰", "❤️")
        if any(b in r for b in bright):
            return True, f"ответ ярче настроения (mood={m})"

    # 6. Ремарки, которые не поймал regex (редкие формы)
    if re.search(r'\b(лад[аеуыой]|она)\b\s+(?:смотр|опуска|кива|улыб|вздых|красне|молч)',
                 r, re.IGNORECASE):
        return True, "третье лицо в реплике"

    return False, ""


# ─────────────── сам вызов ───────────────

_SYSTEM = (
    "Ты — корректор ответов Лады. Она живая девушка, не ассистент. "
    "Тебе дают её ЧЕРНОВОЙ ответ и причину, почему он подозрительный. "
    "Перепиши его так, чтобы он был:\n"
    "  — короче и конкретнее,\n"
    "  — от первого лица, без ремарок о себе,\n"
    "  — в её стиле (живой, без канцелярита),\n"
    "  — без вопроса в конце, если только это не прямая просьба,\n"
    "  — без эмодзи в каждой строке (1-2 максимум),\n"
    "  — отвечающий по сути на сообщение собеседника.\n"
    "\n"
    "Верни ТОЛЬКО итоговый текст, без пояснений и без кавычек. "
    "Если черновой ответ уже нормальный — верни его же без изменений."
)


async def rewrite_if_suspicious(
    reply: str,
    user_msg: str,
    history: list,
    had_regex_strip: bool = False,
    had_fake_strip: bool = False,
) -> tuple:
    """
    Возвращает (final_reply, was_rewritten: bool, reason: str).
    """
    if not SELF_CHECK_ENABLED:
        return reply, False, "disabled"

    suspicious, reason = _is_suspicious(
        reply, user_msg, had_regex_strip, had_fake_strip
    )
    if not suspicious:
        return reply, False, ""

    # Собираем короткий контекст — только последние 4 сообщения
    ctx_lines = []
    for m in (history or [])[-4:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        name = "Он" if role == "user" else "Лада"
        content = (m.get("content") or "").strip()[:200]
        if content and content != reply:
            ctx_lines.append(f"{name}: {content}")

    user_block = (
        f"Последние сообщения:\n" + "\n".join(ctx_lines) + "\n\n"
        f"Текущее сообщение собеседника: {user_msg[:300]}\n\n"
        f"Черновой ответ Лады:\n{reply[:700]}\n\n"
        f"Причина подозрения: {reason}\n\n"
        f"Перепиши."
    )

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user_block},
    ]

    try:
        try:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                think=False, keep_alive="2m",
                options={"temperature": 0.4, "num_predict": 400},
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                keep_alive="2m",
                options={"temperature": 0.4, "num_predict": 400},
            )
    except Exception as e:
        print(f"   ⚠️ self_check LLM: {e}")
        return reply, False, f"llm error: {e}"

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    # Минимальная чистка
    content = content.strip().strip('"').strip("'").strip()

    # Санити: если стало пусто или слишком коротко — оставляем оригинал
    if not content or len(content) < 3:
        return reply, False, f"пусто после rewrite ({reason})"

    # Если переписанный ответ почти идентичен — считаем не переписанным
    if content == reply.strip():
        return reply, False, f"без изменений ({reason})"

    return content, True, reason