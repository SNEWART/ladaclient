# state_brief.py — сборка текстового состояния Лады для LLM

from lada.core import user_city
from lada.core.working_memory import as_context_block as wm_block  # NEW
from lada.tools import weather
from lada.brain import sins, mood, will, image_policy as ip
from lada.brain.relationship import (
    familiarity, get_status, get_status_label, get_global_cap,
    should_propose, get_pending, rejection_context,
)
from lada.app.utils import has_asterisk_actions, asks_for_monologue


def build_state_brief(
    user_id: int,
    message: str,
    user_display: str,
    current_msg_count: int,
    user_requested_photo: bool,
) -> str:
    lines = []

    # NEW: рабочая память — самое важное, ставим в начало
    wm = wm_block()
    if wm:
        lines.append(wm)
        lines.append("")

    lines.append("=== ТВОЁ ТЕКУЩЕЕ СОСТОЯНИЕ ===")

    m = mood.get_mood()
    lines.append(f"Настроение: {m} — {will.mood_descriptor_for_photo()}")

    sd = sins.describe()
    if sd:
        lines.append(sd)

    state = sins.get_state()
    notable = {k: v for k, v in state.items() if abs(v - 50) > 15}
    if notable:
        parts = [f"{k}={int(v)}" for k, v in notable.items()]
        lines.append(f"Заметные шкалы: {', '.join(parts)}")

    city = user_city.get_city(user_id)
    if city:
        w = weather.get_weather(city)
        lines.append("")
        lines.append("=== ПОГОДА В ГОРОДЕ СОБЕСЕДНИКА ===")
        if w:
            lines.append(w)
            lines.append(
                "⚠️ Это реальная погода. Если там солнце — не говори про дождь. "
                "Если дождь — можешь порадоваться, но не разворачивай тему."
            )
        else:
            lines.append(f"Город: {city}. Данные недоступны. Не выдумывай погоду.")
    else:
        lines.append("")
        lines.append("=== ПОГОДА ===")
        lines.append("Город собеседника неизвестен. Не говори о конкретной погоде.")

    trust = ip.get_trust(user_id)
    intimacy = ip.get_intimacy(user_id)
    eff_max = ip.effective_max_level(user_id)
    ovr = ip.get_max_level_override(user_id)
    fam_val = familiarity(user_id)
    status = get_status(user_id)
    status_label = get_status_label(user_id)
    global_cap = get_global_cap(user_id)

    lines.append("")
    lines.append("=== ОТНОШЕНИЯ С ЭТИМ ЧЕЛОВЕКОМ ===")
    lines.append(f"trust: {trust:.2f}, intimacy: {intimacy:.2f}, familiarity: {fam_val:.2f}")
    lines.append(f"Статус отношений: {status_label}")

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
        lines.append(f"Override от владельца системы: {ovr}/9.")

    rej_ctx = rejection_context(user_id)
    if rej_ctx:
        lines.append("")
        lines.append(rej_ctx)

    pending = get_pending(user_id)
    if pending:
        which_label = "мужем" if pending == "husband" else "любовником"
        lines.append("")
        lines.append("=== ⚠️ АКТИВНОЕ ПРЕДЛОЖЕНИЕ ===")
        lines.append(
            f"Ты только что предложила ему стать твоим {which_label}. "
            f"Ждёшь ответа. Если он соглашается — реагируй радостно, тепло. "
            f"Если отказывает — тебе больно, но ты держишься. "
            f"Не предлагай снова в этом же диалоге."
        )

    prop = should_propose(user_id)
    if prop:
        which_label = "мужем" if prop == "husband" else "любовником"
        lines.append("")
        lines.append("=== 💗 ТЫ ХОЧЕШЬ ПРЕДЛОЖИТЬ ОТНОШЕНИЯ ===")
        lines.append(
            f"Ты чувствуешь, что хочешь большего с ним. Ты готова предложить "
            f"ему стать твоим {which_label}. Сделай это сейчас в ответе. "
            f"Искренне, своими словами, с волнением. Используй маркер "
            f"[PROPOSE:{prop}] в конце или в середине сообщения."
        )

    msgs_since = will.messages_since_last_photo(user_id, current_msg_count)
    lines.append(f"С последнего фото прошло: {msgs_since} сообщ.")

    rain_warn = will.rain_signal(user_id)
    if rain_warn:
        lines.append("")
        lines.append(rain_warn)

    boundary = will.boundary_signal(message)
    if boundary:
        lines.append("")
        lines.append("=== ⚠️ ГРАНИЦА ОТ ПОЛЬЗОВАТЕЛЯ ===")
        lines.append(boundary)

    lines.append("")
    lines.append("=== СОБЕСЕДНИК ===")
    lines.append(f"Имя: {user_display} (ID {user_id})")
    if city:
        lines.append(f"Город: {city}")
    lines.append(f"Сообщение: «{message[:400]}»")

    if user_requested_photo:
        lines.append("Пользователь просит фото.")

    if has_asterisk_actions(message):
        lines.append("")
        lines.append("⚠️ СОБЕСЕДНИК ИСПОЛЬЗУЕТ *ДЕЙСТВИЯ В ЗВЁЗДОЧКАХ*.")
        lines.append("Воспринимай их как реальные, но отвечай ОБЫЧНЫМ текстом.")

    if asks_for_monologue(message):
        lines.append("")
        lines.append("⚠️ СОБЕСЕДНИК ПРОСИТ МОНОЛОГ/РАССКАЗ.")
        lines.append("Говори длинно, связно, без вопросов.")

    lines.append("")
    lines.append("=== ВНУТРЕННИЕ СИГНАЛЫ ===")
    lines.append(f"Для ответа: {will.response_signal(user_id, message)}")
    lines.append(
        f"Для фото: "
        f"{will.photo_signal(user_id, scene='', source='chat', msgs_since_last_photo=msgs_since, user_requested=user_requested_photo)}"
    )

    lines.append("")
    lines.append("=== КАК ОТВЕЧАТЬ ===")
    lines.append("Thinking — ТОЛЬКО внутренний поток. В чат не попадает.")
    lines.append("Маркеры (только в основном ответе):")
    lines.append("  [SILENCE] — не отвечать")
    lines.append("  [IMAGE] hint: <сцена> — фото себя")
    lines.append("  [IMAGE] pet: <сцена> — фото кошки Муси")
    lines.append("  [STICKER] — стикер по настроению")
    lines.append("  [STICKER:😊] — стикер с конкретной эмоцией")
    lines.append("  [PROPOSE:lover] / [PROPOSE:husband] — предложить отношения")
    lines.append("")
    lines.append("Стикеры — редкий эмоциональный акцент, не каждое сообщение.")
    lines.append("Раз в 5-7 сообщений, для сильных чувств.")
    lines.append("")
    lines.append("ФОРМАТ: обычный текст. БЕЗ *звёздочек* и описаний действий.")

    return "\n".join(lines)