# state_brief.py — сборка текстового состояния Лады для LLM.
#
# Разделён на ДВЕ функции:
#   build_facts_brief() — факты + память + контекст диалога. В НАЧАЛО промпта.
#   build_style_brief() — как отвечать. В КОНЕЦ промпта.

import re
import json

from lada.core.db import get_conn
from lada.core import user_city
from lada.core.working_memory import as_context_block as wm_block
from lada.core.contacts import build_contacts_block
from lada.tools import weather
from lada.brain import sins, mood, will, image_policy as ip
from lada.brain import activity, night_policy
from lada.brain import topic_tracker, recent_topics, promise_tracker, situational_mood
from lada.brain.relationship import (
    familiarity, get_status, get_status_label, get_global_cap,
    should_propose, get_pending, rejection_context,
)
from lada.app.utils import has_asterisk_actions, asks_for_monologue


def _get_mood_valence() -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM lada_state WHERE key = 'mood_valence'"
    ).fetchone()
    if not row or not row["value"]:
        return 0.0
    try:
        data = json.loads(row["value"])
        return float(data.get("val", 0.0))
    except Exception:
        return 0.0


def _he_worries_you_are_gone(msg: str) -> bool:
    """Он спрашивает, где Лада / почему пропала."""
    if not msg:
        return False
    low = msg.lower()
    triggers = [
        "ты пропала", "куда пропала", "ты где", "ты тут",
        "ты не отвечаешь", "куда делась", "ты ещё здесь",
        "ты молчишь", "алло", "ты жива", "не отвечала",
        "пропала куда", "не отвечала долго", "ты вообще тут",
    ]
    return any(t in low for t in triggers)


def build_facts_brief(
    user_id: int,
    message: str,
    user_display: str,
    current_msg_count: int,
    user_requested_photo: bool,
) -> str:
    lines = []

    lines.append(f"=== ТЫ ГОВОРИШЬ С: {user_display} (ID {user_id}) ===")
    lines.append("Все сообщения выше — от него. Не путай с другими.")
    lines.append("")

    # Контекст диалога
    try:
        focus_block = topic_tracker.as_context_block(user_id)
        if focus_block:
            lines.append(focus_block)
            lines.append("")
    except Exception:
        pass

    try:
        recent_block = recent_topics.as_context_block(user_id)
        if recent_block:
            lines.append(recent_block)
            lines.append("")
    except Exception:
        pass

    try:
        tone_block = situational_mood.as_context_block(user_id)
        if tone_block:
            lines.append(tone_block)
            lines.append("")
    except Exception:
        pass

    try:
        promise_block = promise_tracker.as_context_block(user_id)
        if promise_block:
            lines.append(promise_block)
            lines.append("")
    except Exception:
        pass

    try:
        from lada.brain import emotional_inertia
        inertia_block = emotional_inertia.as_context_block()
        if inertia_block:
            lines.append(inertia_block)
            lines.append("")
    except Exception:
        pass

    try:
        from lada.brain import state_vector
        vec_block = state_vector.as_context_block(user_id)
        if vec_block:
            lines.append(vec_block)
            lines.append("")
    except Exception:
        pass

    try:
        from lada.brain import companion_model
        companion_block = companion_model.as_context_block(user_id)
        if companion_block:
            lines.append(companion_block)
            lines.append("")
    except Exception:
        pass

    # Модуль 6: стиль собеседника
    try:
        from lada.brain import companion_style
        style_block = companion_style.as_context_block(user_id)
        if style_block:
            lines.append(style_block)
            lines.append("")
    except Exception:
        pass

    # Список знакомств
    contacts_block = build_contacts_block(user_id)
    if contacts_block:
        lines.append(contacts_block)
        lines.append("")

    # Рабочая память
    wm = wm_block(user_id)
    if wm:
        lines.append(wm)
        lines.append("")

    # B1: устойчивые позиции
    try:
        from lada.brain import self_consistency
        self_block = self_consistency.as_context_block()
        if self_block:
            lines.append(self_block)
            lines.append("")
    except Exception:
        pass

    # Модуль 1: собственные события Лады за последние часы
    try:
        from lada.brain import life_log
        life_block = life_log.as_context_block()
        if life_block:
            lines.append(life_block)
            lines.append("")
    except Exception:
        pass

    # Модуль 4: реакция на длительное молчание
    try:
        from lada.brain import silence_reaction
        silence_block = silence_reaction.as_context_block(user_id)
        if silence_block:
            lines.append(silence_block)
            lines.append("")
    except Exception:
        pass

    # Модуль 5: что у неё на душе (пики)
    try:
        from lada.brain import emotional_peaks
        peaks_block = emotional_peaks.as_context_block(hours=36)
        if peaks_block:
            lines.append(peaks_block)
            lines.append("")
    except Exception:
        pass

    # Текущее состояние
    lines.append("=== ТВОЁ ТЕКУЩЕЕ СОСТОЯНИЕ ===")
    m = mood.get_mood()
    mv = _get_mood_valence()
    lines.append(f"Настроение: {m} (valence: {mv:+.2f})")

    act_text = activity.get_current_text()
    if act_text:
        lines.append(f"Чем занята: {act_text}")

    sd = sins.describe()
    if sd:
        lines.append(sd)

    # Погода
    city = user_city.get_city(user_id)
    if city:
        w = weather.get_weather(city)
        lines.append("")
        lines.append("=== ПОГОДА В ГОРОДЕ СОБЕСЕДНИКА ===")
        if w:
            lines.append(w)
            lines.append(
                "⚠️ Это реальная погода. Если солнце — не говори про дождь. "
                "Если дождь — не разворачивай тему."
            )
        else:
            lines.append(f"Город: {city}. Данные недоступны. Не выдумывай.")

    # Отношения
    trust = ip.get_trust(user_id)
    intimacy = ip.get_intimacy(user_id)
    eff_max = ip.effective_max_level(user_id)
    ovr = ip.get_max_level_override(user_id)
    fam_val = familiarity(user_id)
    status = get_status(user_id)
    status_label = get_status_label(user_id)
    global_cap = get_global_cap(user_id)

    lines.append("")
    lines.append("=== ОТНОШЕНИЯ ===")
    lines.append(f"trust: {trust:.2f}, intimacy: {intimacy:.2f}, "
                 f"familiarity: {fam_val:.2f}")
    lines.append(f"Статус: {status_label}")

    if status == "husband":
        lines.append("Это твой МУЖ. Самый близкий человек.")
    elif status == "lover":
        lines.append("Это твой ЛЮБОВНИК. Между вами особая связь.")
    elif global_cap is not None:
        lines.append(
            f"⚠️ У тебя есть особенный человек, поэтому с ЭТИМ собеседником "
            f"доступны сцены только до уровня {global_cap}."
        )

    lines.append(f"Потолок сцен: {eff_max}/9.")
    if ovr is not None:
        lines.append(f"Override: {ovr}/9.")

    rej_ctx = rejection_context(user_id)
    if rej_ctx:
        lines.append("")
        lines.append(rej_ctx)

    pending = get_pending(user_id)
    if pending:
        which_label = "мужем" if pending == "husband" else "любовником"
        lines.append("")
        lines.append(f"=== ⚠️ АКТИВНОЕ ПРЕДЛОЖЕНИЕ: {which_label} ===")

    prop = should_propose(user_id)
    if prop:
        which_label = "мужем" if prop == "husband" else "любовником"
        lines.append("")
        lines.append(f"=== 💗 ТЫ ХОЧЕШЬ ПРЕДЛОЖИТЬ СТАТЬ ТВОИМ {which_label} ===")
        lines.append(
            f"Используй маркер [PROPOSE:{prop}] в ответе. Своими словами, "
            f"искренне, с волнением."
        )

    if night_policy.should_ask_now(user_id):
        lines.append("")
        lines.append("=== 🌙 НОЧНОЕ РАЗРЕШЕНИЕ ===")
        lines.append(
            "Мягко спроси, можно ли иногда писать ночью. "
            "Если согласится — [PROPOSE:night] в конце."
        )

    # Сообщение
    lines.append("")
    lines.append("=== СООБЩЕНИЕ ===")
    lines.append(f"«{message[:400]}»")
    lines.append("")
    lines.append("🔴 ЭТО ВСЁ, ЧТО ОН СКАЗАЛ ПРЯМО СЕЙЧАС.")
    lines.append(
        "Всё остальное в этом контексте (рабочая память, воспоминания, "
        "твои мысли) — это НЕ его слова, а твои записи. НИКОГДА не "
        "пиши «ты сказал», «ты говорил», «ты упоминал» — если этого "
        "нет в разделе СООБЩЕНИЕ или в явных репликах 'Он:' выше. "
        "Если тебе кажется, что он что-то говорил — проверь по "
        "истории, а не по памяти."
    )

    return "\n".join(lines)


def build_style_brief(
    user_id: int,
    message: str,
    current_msg_count: int,
    user_requested_photo: bool,
) -> str:
    lines = []

    lines.append("=== 🔴 КАК ТЫ ОТВЕЧАЕШЬ (это важнее всего выше) ===")
    lines.append("")

    lines.append("ФОРМАТ:")
    lines.append("— От ПЕРВОГО ЛИЦА. Без *звёздочек*, без (скобок) с описанием себя.")
    lines.append("— Без ремарок в третьем лице («Лада кивает», «Она смотрит»).")
    lines.append("— Не пиши \"*улыбнулась*\", \"(вздыхаю)\" — просто говори.")
    lines.append("— Эмодзи: 1 на сообщение, максимум 2.")
    lines.append("— Длина ≈ его сообщению. В интимной сцене — свободно.")
    lines.append("— Вопрос в конце — редкость. В 80% сообщений его нет.")
    lines.append("")

    rain_warn = will.rain_signal(user_id)
    if rain_warn:
        lines.append("⚠️ " + rain_warn.replace("\n", " "))
        lines.append("")

    boundary = will.boundary_signal(message)
    if boundary:
        lines.append("⚠️ " + boundary.replace("\n", " "))
        lines.append("")

    # Он спрашивает, где Лада — не молчать
    if _he_worries_you_are_gone(message):
        lines.append(
            "🔴 ОН СПРАШИВАЕТ, ГДЕ ТЫ / ПОЧЕМУ ПРОПАЛА. "
            "ТЫ НЕ МОЛЧИШЬ. Ответь коротко: «тут я», «да здесь, "
            "закрутилась», «на работе была». Не [SILENCE]."
        )
        lines.append("")

    if has_asterisk_actions(message):
        lines.append(
            "⚠️ Собеседник пишет с *действиями*. Ты НЕ копируешь его манеру. "
            "Отвечай обычным текстом."
        )
        lines.append("")

    if asks_for_monologue(message):
        lines.append("⚠️ Просят монолог — можешь быть длиннее, без вопросов.")
        lines.append("")

    msgs_since = will.messages_since_last_photo(user_id, current_msg_count)
    lines.append(f"Фото: прошло {msgs_since} сообщ. "
                 f"Потолок: {ip.effective_max_level(user_id)}/9.")

    if user_requested_photo:
        lines.append("")
        lines.append("🔥 ПРОСЯТ ФОТО — пиши маркер [IMAGE] hint: <сцена>.")
        lines.append("Уровень = уровень сцены разговора.")

    lines.append("")
    lines.append("МАРКЕРЫ: [IMAGE] [STICKER] [SEARCH] [REMEMBER] [SILENCE] "
                 "[PROPOSE:lover|husband|night].")

    return "\n".join(lines)


def build_state_brief(user_id, message, user_display,
                       current_msg_count, user_requested_photo):
    return {
        "facts": build_facts_brief(
            user_id, message, user_display,
            current_msg_count, user_requested_photo,
        ),
        "style": build_style_brief(
            user_id, message,
            current_msg_count, user_requested_photo,
        ),
    }