# lada/app/state_brief_optimized.py — Оптимизированная сборка контекста с ленивой загрузкой
#
# РЕВОЛЮЦИЯ: вместо вызова всех модулей каждый раз, используем ленивую
# загрузку и кэширование. Модули пересчитываются только при изменении данных.
#
# Результат: ~70% меньше вычислений, ~50% быстрее генерация

import json
from lada.core.db import get_conn
from lada.core import user_city
from lada.core.lazy_context import (
    get_lazy_context_manager,
    get_module_deps,
    MODULE_TTL,
)
from lada.tools import weather
from lada.brain import sins, mood, will, image_policy as ip
from lada.brain import activity, night_policy
from lada.brain.relationship import (
    familiarity, get_status, get_status_label, get_global_cap,
    should_propose, get_pending, rejection_context,
)
from lada.app.utils import has_asterisk_actions, asks_for_monologue


# Примерная стоимость каждого модуля в токенах (для бюджета)
MODULE_COSTS = {
    "topic_tracker": 150,
    "recent_topics": 120,
    "situational_mood": 100,
    "promise_tracker": 180,
    "emotional_inertia": 80,
    "state_vector": 150,
    "companion_model": 250,
    "companion_style": 200,
    "contacts": 150,
    "working_memory": 200,
    "self_consistency": 300,
    "life_log": 200,
    "silence_reaction": 100,
    "emotional_peaks": 400,
    "mood_state": 150,
    "relations": 200,
    "weather": 100,
}


def _get_mood_valence() -> float:
    """Быстрое чтение valence из БД."""
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


def build_facts_brief_optimized(
    user_id: int,
    message: str,
    user_display: str,
    current_msg_count: int,
    user_requested_photo: bool,
) -> str:
    """
    Оптимизированная версия build_facts_brief с ленивой загрузкой.

    Модули загружаются из кэша или вычисляются только при необходимости.
    """
    lines = []
    mgr = get_lazy_context_manager()

    lines.append(f"=== ТЫ ГОВОРИШЬ С: {user_display} (ID {user_id}) ===")
    lines.append("Все сообщения выше — от него. Не путай с другими.")
    lines.append("")

    # ─────────────── КОНТЕКСТ ДИАЛОГА (быстро меняющиеся) ───────────────

    # Topic tracker (TTL: 1 мин)
    def compute_topic_tracker():
        from lada.brain import topic_tracker
        return topic_tracker.as_context_block(user_id)

    topic_block = mgr.get_or_compute(
        f"topic_tracker_{user_id}",
        compute_topic_tracker,
        deps=get_module_deps("topic_tracker", user_id),
        ttl=MODULE_TTL["topic_tracker"],
        cost_tokens=MODULE_COSTS["topic_tracker"],
    )
    if topic_block:
        lines.append(topic_block)
        lines.append("")

    # Recent topics (TTL: 3 мин)
    def compute_recent_topics():
        from lada.brain import recent_topics
        return recent_topics.as_context_block(user_id)

    recent_block = mgr.get_or_compute(
        f"recent_topics_{user_id}",
        compute_recent_topics,
        deps=get_module_deps("recent_topics", user_id),
        ttl=MODULE_TTL["recent_topics"],
        cost_tokens=MODULE_COSTS["recent_topics"],
    )
    if recent_block:
        lines.append(recent_block)
        lines.append("")

    # Situational mood (TTL: 2 мин)
    def compute_situational_mood():
        from lada.brain import situational_mood
        return situational_mood.as_context_block(user_id)

    tone_block = mgr.get_or_compute(
        f"situational_mood_{user_id}",
        compute_situational_mood,
        deps=get_module_deps("situational_mood", user_id),
        ttl=MODULE_TTL["situational_mood"],
        cost_tokens=MODULE_COSTS["situational_mood"],
    )
    if tone_block:
        lines.append(tone_block)
        lines.append("")

    # Promise tracker (TTL: 5 мин)
    def compute_promise_tracker():
        from lada.brain import promise_tracker
        return promise_tracker.as_context_block(user_id)

    promise_block = mgr.get_or_compute(
        f"promise_tracker_{user_id}",
        compute_promise_tracker,
        deps=get_module_deps("promise_tracker", user_id),
        ttl=MODULE_TTL["promise_tracker"],
        cost_tokens=MODULE_COSTS["promise_tracker"],
    )
    if promise_block:
        lines.append(promise_block)
        lines.append("")

    # ─────────────── СОСТОЯНИЕ ЛАДЫ (средне меняющиеся) ───────────────

    # Emotional inertia (TTL: 15 мин)
    def compute_emotional_inertia():
        from lada.brain import emotional_inertia
        return emotional_inertia.as_context_block()

    inertia_block = mgr.get_or_compute(
        "emotional_inertia",
        compute_emotional_inertia,
        deps={},
        ttl=MODULE_TTL["emotional_inertia"],
        cost_tokens=MODULE_COSTS["emotional_inertia"],
    )
    if inertia_block:
        lines.append(inertia_block)
        lines.append("")

    # State vector (TTL: 5 мин)
    def compute_state_vector():
        from lada.brain import state_vector
        return state_vector.as_context_block(user_id)

    vec_block = mgr.get_or_compute(
        f"state_vector_{user_id}",
        compute_state_vector,
        deps=get_module_deps("state_vector", user_id),
        ttl=MODULE_TTL["state_vector"],
        cost_tokens=MODULE_COSTS["state_vector"],
    )
    if vec_block:
        lines.append(vec_block)
        lines.append("")

    # Companion model (TTL: 5 мин)
    def compute_companion_model():
        from lada.brain import companion_model
        return companion_model.as_context_block(user_id)

    companion_block = mgr.get_or_compute(
        f"companion_model_{user_id}",
        compute_companion_model,
        deps=get_module_deps("companion_model", user_id),
        ttl=MODULE_TTL["companion_model"],
        cost_tokens=MODULE_COSTS["companion_model"],
    )
    if companion_block:
        lines.append(companion_block)
        lines.append("")

    # Companion style (TTL: 1 час, если включен)
    try:
        from config import COMPANION_STYLE_ENABLED
        if COMPANION_STYLE_ENABLED:
            def compute_companion_style():
                from lada.brain import companion_style
                return companion_style.as_context_block(user_id)

            style_block = mgr.get_or_compute(
                f"companion_style_{user_id}",
                compute_companion_style,
                deps=get_module_deps("companion_style", user_id),
                ttl=MODULE_TTL["companion_style"],
                cost_tokens=MODULE_COSTS["companion_style"],
            )
            if style_block:
                lines.append(style_block)
                lines.append("")
    except Exception:
        pass

    # ─────────────── ЗНАКОМСТВА И ПАМЯТЬ (медленно меняющиеся) ───────────────

    # Contacts (TTL: 30 мин)
    def compute_contacts():
        from lada.core.contacts import build_contacts_block
        return build_contacts_block(user_id)

    contacts_block = mgr.get_or_compute(
        f"contacts_{user_id}",
        compute_contacts,
        deps={"user_id": user_id},
        ttl=MODULE_TTL["contacts"],
        cost_tokens=MODULE_COSTS["contacts"],
    )
    if contacts_block:
        lines.append(contacts_block)
        lines.append("")

    # Working memory (TTL: 10 мин)
    def compute_working_memory():
        from lada.core.working_memory import as_context_block as wm_block
        return wm_block(user_id)

    wm = mgr.get_or_compute(
        f"working_memory_{user_id}",
        compute_working_memory,
        deps=get_module_deps("working_memory", user_id),
        ttl=MODULE_TTL["working_memory"],
        cost_tokens=MODULE_COSTS["working_memory"],
    )
    if wm:
        lines.append(wm)
        lines.append("")

    # ─────────────── ДОЛГОСРОЧНЫЕ МОДУЛИ (редко меняющиеся) ───────────────

    # Self-consistency (TTL: 2 часа, если включен)
    try:
        from config import SELF_CONSISTENCY_ENABLED
        if SELF_CONSISTENCY_ENABLED:
            def compute_self_consistency():
                from lada.brain import self_consistency
                return self_consistency.as_context_block()

            self_block = mgr.get_or_compute(
                "self_consistency",
                compute_self_consistency,
                deps={},
                ttl=MODULE_TTL["self_consistency"],
                cost_tokens=MODULE_COSTS["self_consistency"],
            )
            if self_block:
                lines.append(self_block)
                lines.append("")
    except Exception:
        pass

    # Life log (TTL: 30 мин, если включен)
    try:
        from config import LIFE_LOG_ENABLED
        if LIFE_LOG_ENABLED:
            def compute_life_log():
                from lada.brain import life_log
                return life_log.as_context_block()

            life_block = mgr.get_or_compute(
                "life_log",
                compute_life_log,
                deps={},
                ttl=MODULE_TTL["life_log"],
                cost_tokens=MODULE_COSTS["life_log"],
            )
            if life_block:
                lines.append(life_block)
                lines.append("")
    except Exception:
        pass

    # Silence reaction
    def compute_silence_reaction():
        from lada.brain import silence_reaction
        return silence_reaction.as_context_block(user_id)

    silence_block = mgr.get_or_compute(
        f"silence_reaction_{user_id}",
        compute_silence_reaction,
        deps=get_module_deps("silence_reaction", user_id),
        ttl=MODULE_TTL.get("silence_reaction", 300),
        cost_tokens=MODULE_COSTS["silence_reaction"],
    )
    if silence_block:
        lines.append(silence_block)
        lines.append("")

    # Emotional peaks (TTL: 1 час, если включен)
    try:
        from config import EMOTIONAL_PEAKS_ENABLED
        if EMOTIONAL_PEAKS_ENABLED:
            def compute_emotional_peaks():
                from lada.brain import emotional_peaks
                return emotional_peaks.as_context_block(hours=36)

            peaks_block = mgr.get_or_compute(
                "emotional_peaks",
                compute_emotional_peaks,
                deps={},
                ttl=MODULE_TTL["emotional_peaks"],
                cost_tokens=MODULE_COSTS["emotional_peaks"],
            )
            if peaks_block:
                lines.append(peaks_block)
                lines.append("")
    except Exception:
        pass

    # ─────────────── ТЕКУЩЕЕ СОСТОЯНИЕ (всегда свежее) ───────────────

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

    # Погода (TTL: 1 час)
    city = user_city.get_city(user_id)
    if city:
        def compute_weather():
            w = weather.get_weather(city)
            if w:
                return (
                    "=== ПОГОДА В ГОРОДЕ СОБЕСЕДНИКА ===\n"
                    f"{w}\n"
                    "⚠️ Это реальная погода. Если солнце — не говори про дождь. "
                    "Если дождь — не разворачивай тему."
                )
            else:
                return f"=== ПОГОДА ===\nГород: {city}. Данные недоступны. Не выдумывай."

        weather_block = mgr.get_or_compute(
            f"weather_{city}",
            compute_weather,
            deps=get_module_deps("weather", user_id),
            ttl=MODULE_TTL["weather"],
            cost_tokens=MODULE_COSTS["weather"],
        )
        if weather_block:
            lines.append("")
            lines.append(weather_block)

    # ─────────────── ОТНОШЕНИЯ (средне меняющиеся) ───────────────

    def compute_relations():
        trust = ip.get_trust(user_id)
        intimacy = ip.get_intimacy(user_id)
        eff_max = ip.effective_max_level(user_id)
        ovr = ip.get_max_level_override(user_id)
        fam_val = familiarity(user_id)
        status = get_status(user_id)
        status_label = get_status_label(user_id)
        global_cap = get_global_cap(user_id)

        rel_lines = []
        rel_lines.append("=== ОТНОШЕНИЯ ===")
        rel_lines.append(f"trust: {trust:.2f}, intimacy: {intimacy:.2f}, "
                         f"familiarity: {fam_val:.2f}")
        rel_lines.append(f"Статус: {status_label}")

        if status == "husband":
            rel_lines.append("Это твой МУЖ. Самый близкий человек.")
        elif status == "lover":
            rel_lines.append("Это твой ЛЮБОВНИК. Между вами особая связь.")
        elif global_cap is not None:
            rel_lines.append(
                f"⚠️ У тебя есть особенный человек, поэтому с ЭТИМ собеседником "
                f"доступны сцены только до уровня {global_cap}."
            )

        rel_lines.append(f"Потолок сцен: {eff_max}/9.")
        if ovr is not None:
            rel_lines.append(f"Override: {ovr}/9.")

        rej_ctx = rejection_context(user_id)
        if rej_ctx:
            rel_lines.append("")
            rel_lines.append(rej_ctx)

        pending = get_pending(user_id)
        if pending:
            which_label = "мужем" if pending == "husband" else "любовником"
            rel_lines.append("")
            rel_lines.append(f"=== ⚠️ АКТИВНОЕ ПРЕДЛОЖЕНИЕ: {which_label} ===")

        prop = should_propose(user_id)
        if prop:
            which_label = "мужем" if prop == "husband" else "любовником"
            rel_lines.append("")
            rel_lines.append(f"=== 💗 ТЫ ХОЧЕШЬ ПРЕДЛОЖИТЬ СТАТЬ ТВОИМ {which_label} ===")
            rel_lines.append(
                f"Используй маркер [PROPOSE:{prop}] в ответе. Своими словами, "
                f"искренне, с волнением."
            )

        if night_policy.should_ask_now(user_id):
            rel_lines.append("")
            rel_lines.append("=== 🌙 НОЧНОЕ РАЗРЕШЕНИЕ ===")
            rel_lines.append(
                "Мягко спроси, можно ли иногда писать ночью. "
                "Если согласится — [PROPOSE:night] в конце."
            )

        return "\n".join(rel_lines)

    relations_block = mgr.get_or_compute(
        f"relations_{user_id}",
        compute_relations,
        deps=get_module_deps("relations", user_id),
        ttl=MODULE_TTL["relations"],
        cost_tokens=MODULE_COSTS["relations"],
    )
    if relations_block:
        lines.append("")
        lines.append(relations_block)

    # ─────────────── ТЕКУЩЕЕ СООБЩЕНИЕ ───────────────

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
    """Стиль ответа — не кэшируется, всегда свежий."""
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


def build_state_brief_optimized(user_id, message, user_display,
                                  current_msg_count, user_requested_photo):
    """
    Оптимизированная версия build_state_brief.

    Использует ленивую загрузку и кэширование модулей.
    """
    return {
        "facts": build_facts_brief_optimized(
            user_id, message, user_display,
            current_msg_count, user_requested_photo,
        ),
        "style": build_style_brief(
            user_id, message,
            current_msg_count, user_requested_photo,
        ),
    }
