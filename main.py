# main.py — точка входа

import asyncio
import re
from datetime import datetime
from functools import partial

import uvicorn
from telethon import TelegramClient, events, connection

from config import (
    API_ID, API_HASH, OWNER_ID,
    SHOW_RAW_EVENTS, MAX_HISTORY, REFLECTION_INTERVAL,
    FAISS_TOP_K, FAISS_MIN_SCORE, FAISS_MAX_CHARS,
    WEB_SEARCH_ENABLED, WEB_SEARCH_MAX_ROUNDS,
    EMOTION_ENABLED, SLEEP_INTERVAL_HOURS, COOLDOWN_SECONDS, IDLE_MINUTES,
    MONOLOGUE_INTERVAL_MINUTES, MONOLOGUE_FIRST_DELAY_MINUTES,
    WEB_PORT, QUEUE_WORKER_COUNT, QUEUE_MAX_SIZE, QUEUE_DEBOUNCE_SECS,
    INITIATIVE_ENABLED, INITIATIVE_CHECK_INTERVAL_MINUTES,
)
from lada.core.llm import ask, ask_text
from lada.core.memory import get_history, add_message
from lada.core.faiss_memory import faiss_mem
from lada.core.persona import get_persona
from lada.core import users_names, user_city
from lada.brain.reflection import reflection_loop
from lada.brain.emotions import evaluate_and_record
from lada.brain.initiative import initiative_loop, touch_user_activity
from lada.brain.sleep import sleep_loop, consolidate
from lada.brain.monologue import monologue_loop, think_once, get_recent_thoughts
from lada.brain import relationship as rel
from lada.brain.relationship import (
    touch_first_seen, familiarity_label, familiarity, emotion_impact_note,
    get_status, get_status_label, get_global_cap, set_status,
    should_propose, record_proposal, accept_proposal, reject_proposal,
    get_pending, rejection_context,
)
from lada.brain import sins, mood, image_policy, image_mood, will
from lada.telegram.mode import should_reply, get_mode, set_mode
from lada.telegram.media_processor import process_media
from lada.telegram.cooldown import is_allowed, touch
from lada.telegram.presence import presence_loop, set_online, touch_activity
from lada.telegram.queue import MessageQueue, QueuedMessage
from lada.telegram import splitter, stickers
from lada.tools import web_search, weather
from lada.tools import image_gen

client = TelegramClient(
    "lada_session", API_ID, API_HASH,
    connection=connection.ConnectionTcpMTProxyAbridged,
    proxy=("146.66.180.10", 1443, "dd5a171693fa51f091fbcb91bfa8c0f697")
)

message_queue = MessageQueue(
    worker_count=QUEUE_WORKER_COUNT,
    max_size=QUEUE_MAX_SIZE,
    debounce_secs=QUEUE_DEBOUNCE_SECS,
)

_recent_photo_prompts: list[str] = []

NOISY_EVENTS = {
    "UpdateUserStatus", "UpdateUserTyping", "UpdateChatUserTyping",
    "UpdateChatParticipants", "UpdateReadHistoryInbox", "UpdateReadHistoryOutbox",
    "UpdateReadMessagesContents", "UpdateChannelReadMessagesContents",
    "UpdatePts", "UpdateNewScheduledMessage", "UpdateMessageID",
    "UpdateMessagePollVote", "UpdateChatOnlineMembers",
}


def log(text: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")


def log_thinking(thinking: str, limit: int = 400):
    if not thinking:
        return
    t = thinking.strip()
    if len(t) > limit:
        t = t[:limit] + "…"
    print(f"   🧠 thinking: {t}")


def _has_asterisk_actions(text: str) -> bool:
    return bool(re.search(r'\*[^*\n]{2,60}\*', text))


def _asks_for_monologue(text: str) -> bool:
    low = text.lower()
    markers = [
        "расскажи сказку", "расскажи историю", "расскажи что-нибудь",
        "расскажи мне", "продолжай без меня", "продолжи без меня",
        "говори сам", "говори сама", "монолог", "без меня продолжай",
        "не задавай вопросов", "просто расскажи", "развлеки меня",
    ]
    return any(m in low for m in markers)


YES_MARKERS = [
    "да", "конечно", "согласен", "согласна", "хочу", "буду",
    "ок", "окей", "давай", "естественно", "ага", "угу",
    "да, буду", "да, хочу", "да, согласен", "да, согласна",
]
NO_MARKERS = [
    "нет", "не хочу", "не готов", "не готова", "не сейчас",
    "извини, но нет", "прости, нет", "не могу", "не буду",
    "нет, спасибо", "не для меня", "откажусь", "не надо",
]


def _detect_yes_no(text: str) -> str | None:
    if not text:
        return None
    low = text.lower().strip()
    for m in NO_MARKERS:
        if m in low:
            return "no"
    for m in YES_MARKERS:
        if low == m or low.startswith(m + " ") or low.startswith(m + ",") or low.startswith(m + "!") or low.startswith(m + "."):
            return "yes"
    words = low.split()
    if len(words) <= 3:
        for m in YES_MARKERS:
            if m in words:
                return "yes"
        for m in NO_MARKERS:
            if m in words:
                return "no"
    return None


def build_state_brief(
    user_id: int,
    message: str,
    user_display: str,
    current_msg_count: int,
    user_requested_photo: bool,
) -> str:
    from lada.brain import image_policy as ip

    lines = []
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

    # ─── ПОГОДА ───
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

    # ─── Контроль «дождь» ───
    rain_warn = will.rain_signal(user_id, history)
    if rain_warn:
        lines.append("")
        lines.append(rain_warn)

    # ─── Границы ───
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

    if _has_asterisk_actions(message):
        lines.append("")
        lines.append("⚠️ СОБЕСЕДНИК ИСПОЛЬЗУЕТ *ДЕЙСТВИЯ В ЗВЁЗДОЧКАХ*.")
        lines.append("Воспринимай их как реальные. Можешь ответить тем же стилем.")

    if _asks_for_monologue(message):
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
    lines.append("Раз в 5-7 сообщений, для сильных чувств. Не как ответ на вопрос.")

    return "\n".join(lines)


if SHOW_RAW_EVENTS:
    @client.on(events.Raw())
    async def raw_handler(update):
        name = type(update).__name__
        if name in NOISY_EVENTS:
            return
        print(f"   · {name}")


async def start_web():
    from web import app
    config = uvicorn.Config(
        app, host="0.0.0.0", port=WEB_PORT,
        log_level="warning", loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def sins_regression_loop():
    while True:
        await asyncio.sleep(3600)
        try:
            sins.regress_toward_neutral(0.5)
        except Exception as e:
            print(f"⚠️ Ошибка регрессии шкал: {e}")


# ─────────────── Команды владельца ───────────────

async def handle_owner_command(text: str, event) -> bool:
    text = text.strip()
    owner_id = OWNER_ID

    if text.startswith("/mode"):
        parts = text.split()
        if len(parts) == 2 and parts[1] in ("all", "owner"):
            set_mode(parts[1])
            await event.respond(f"✅ Режим: **{parts[1]}**")
        else:
            current = get_mode()
            await event.respond(f"Текущий режим: **{current}**\nИспользование: `/mode all` или `/mode owner`.")
        return True

    if text.startswith("/city"):
        parts = text.split(maxsplit=2)
        if len(parts) == 1:
            my = user_city.get_city(owner_id)
            await event.respond(
                f"Твой город: **{my or 'не задан'}**\n"
                f"Использование:\n"
                f"`/city <город>` — задать себе\n"
                f"`/city <user_id> <город>` — задать другому"
            )
        elif len(parts) == 2:
            city = parts[1]
            user_city.set_city(owner_id, city)
            w = weather.get_weather(city, force=True)
            await event.respond(
                f"✅ Твой город: **{city}**\n"
                f"Погода: {w or 'не удалось получить'}"
            )
        else:
            try:
                target = int(parts[1])
                city = parts[2]
                user_city.set_city(target, city)
                w = weather.get_weather(city, force=True)
                name = users_names.display_name(target)
                await event.respond(
                    f"✅ Город для **{name}** (ID {target}): **{city}**\n"
                    f"Погода: {w or 'не удалось получить'}"
                )
            except ValueError:
                await event.respond("Ошибка: ID должен быть числом")
        return True

    if text.startswith("/nocite"):
        parts = text.split()
        if len(parts) == 1:
            user_city.set_city(owner_id, None)
            await event.respond("✅ Твой город сброшен")
        elif len(parts) == 2 and parts[1].lstrip("-").isdigit():
            user_city.set_city(int(parts[1]), None)
            await event.respond(f"✅ Город для ID {parts[1]} сброшен")
        else:
            await event.respond("Использование: `/nocite [user_id]`")
        return True

    if text.startswith("/weather"):
        parts = text.split()
        target = owner_id
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
        city = user_city.get_city(target)
        if not city:
            await event.respond(f"Город для ID {target} не задан.")
            return True
        w = weather.get_weather(city, force=True)
        name = users_names.display_name(target)
        if w:
            await event.respond(f"🌤 **Погода** для {name} ({city}):\n{w}")
        else:
            await event.respond(f"⚠️ Не удалось получить погоду для {city}")
        return True

    if text == "/reflect":
        from lada.brain.reflection import reflect
        await event.respond("🧠 Запускаю рефлексию...")
        await reflect()
        await event.respond("✅ Рефлексия записана.")
        return True

    if text == "/sleep":
        await event.respond("💤 Запускаю консолидацию...")
        result = await consolidate()
        await event.respond(f"💤 {result}")
        return True

    if text == "/think":
        await event.respond("💭 Думаю...")
        thought = await think_once()
        await event.respond(f"💭 _{thought}_" if thought else "Пусто.")
        return True

    if text == "/thoughts":
        recent = [m for m in faiss_mem.metadata if m.get("category") == "monologue"][-10:]
        if not recent:
            await event.respond("📭 Мыслей пока нет.")
        else:
            lines = ["💭 **Последние мысли:**"]
            for it in recent:
                ts = it.get("timestamp", "")[:16].replace("T", " ")
                lines.append(f"_{ts}_ — {it['text'][:200]}")
            await event.respond("\n\n".join(lines))
        return True

    if text == "/clear_thoughts":
        from lada.brain.monologue import clear_all as clear_monologues
        clear_monologues()
        await event.respond("🗑 Все мысли стёрты.")
        return True

    if text == "/soul":
        state = sins.get_state()
        lines = ["⚖️ **Глубинное состояние:**"]
        for k, v in state.items():
            bar = "█" * int(v / 10) + "░" * (10 - int(v / 10))
            lines.append(f"`{bar}` {k}: {int(v)}")
        lines.append(f"\n💭 Настроение: **{mood.get_mood()}**")
        await event.respond("\n".join(lines))
        return True

    if text == "/whoami":
        f = familiarity_label(owner_id)
        await event.respond(f"Ты для меня — **{f}**.")
        return True

    if text == "/observations":
        items = [
            m for m in faiss_mem.metadata
            if m.get("category") == "observation" and m.get("user_id") == owner_id
        ][-20:]
        if not items:
            await event.respond("👁 Наблюдений пока нет.")
        else:
            lines = ["👁 **Что я заметила о тебе:**"]
            for it in items:
                lines.append(f"• {it['text'][:200]}")
            await event.respond("\n".join(lines))
        return True

    if text == "/queue":
        await event.respond(f"📬 В очереди: {message_queue.size()}")
        return True

    if text == "/users":
        from lada.core.memory import list_users
        ids = list_users()
        if not ids:
            await event.respond("📭 Пользователей пока нет.")
        else:
            lines = ["👥 **Пользователи в памяти:**"]
            for uid in ids:
                name = users_names.display_name(uid)
                city = user_city.get_city(uid)
                city_str = f" · {city}" if city else ""
                if name.startswith("ID "):
                    lines.append(f"• {name}{city_str}")
                else:
                    lines.append(f"• {name} — ID {uid}{city_str}")
            await event.respond("\n".join(lines))
        return True

    if text.startswith("/divorce"):
        parts = text.split()
        target = owner_id
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
        name = users_names.display_name(target)
        current_status = get_status(target)
        current_intimacy = image_policy.get_intimacy(target)

        set_status(target, "neutral", note="manual divorce by owner")
        image_policy.set_trust(target, intimacy=0.5)

        await event.respond(
            f"💔 Развод оформлен.\n"
            f"👤 {name} (ID {target})\n"
            f"   статус: {current_status} → neutral\n"
            f"   intimacy: {current_intimacy:.2f} → 0.50\n"
            f"   trust не тронут"
        )
        return True

    if text.startswith("/status"):
        parts = text.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
            name = users_names.display_name(target)
            st = get_status(target)
            pending = get_pending(target)
            pending_str = f" (pending: {pending})" if pending else ""
            await event.respond(
                f"👤 {name} (ID {target}) — статус: **{get_status_label(target)}** "
                f"(`{st}`){pending_str}"
            )
        elif len(parts) == 3 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
            new_status = parts[2].lower()
            if new_status in ("neutral", "acquaintance", "lover", "husband"):
                set_status(target, new_status, note="manual by owner")
                await event.respond(f"✅ Статус для {target}: **{get_status_label(target)}**")
            else:
                await event.respond("Статус: neutral | acquaintance | lover | husband")
        else:
            await event.respond(
                "Использование:\n"
                "`/status <user_id>` — показать\n"
                "`/status <user_id> <neutral|acquaintance|lover|husband>` — задать\n"
                "`/divorce [user_id]` — снять статус и снизить intimacy до 0.5"
            )
        return True

    if text.startswith("/trust"):
        parts = text.split()
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
            if len(parts) == 2:
                t = image_policy.get_trust(target)
                i = image_policy.get_intimacy(target)
                f = familiarity(target)
                lvl = image_policy._max_level_for_user(target)
                eff = image_policy.effective_max_level(target)
                ovr = image_policy.get_max_level_override(target)
                name = users_names.display_name(target)
                st = get_status_label(target)
                ovr_str = f"\n   override: {ovr}/9" if ovr is not None else ""
                cap = get_global_cap(target)
                cap_str = f"\n   global_cap: {cap}/9" if cap is not None else ""
                await event.respond(
                    f"👤 {name} (ID {target})\n"
                    f"   статус: {st}\n"
                    f"   familiarity: {f:.2f}\n"
                    f"   trust: {t:.2f}\n"
                    f"   intimacy: {i:.2f}\n"
                    f"   базовый уровень: {lvl}/9\n"
                    f"   эффективный: {eff}/9{ovr_str}{cap_str}"
                )
            elif len(parts) >= 3:
                try:
                    t = float(parts[2])
                    i = float(parts[3]) if len(parts) >= 4 else None
                    image_policy.set_trust(target, trust=t, intimacy=i)
                    await event.respond(f"✅ Обновлено для ID {target}")
                except ValueError:
                    await event.respond("Ошибка: числа не распознаны")
        else:
            await event.respond(
                "Использование:\n"
                "`/trust <user_id>` — показать\n"
                "`/trust <user_id> <trust> [intimacy]` — задать (0..1)\n"
                "`/level <user_id> <0..9>` — оверрайд макс. уровня\n"
                "`/level <user_id> auto` — снять оверрайд\n"
                "`/status <user_id>` — статус\n"
                "`/divorce [user_id]` — развод\n"
                "`/city [user_id] <город>` — город для погоды"
            )
        return True

    if text.startswith("/level"):
        parts = text.split()
        if len(parts) == 3 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
            val = parts[2].lower()
            if val == "auto":
                image_policy.set_max_level_override(target, None)
                await event.respond(f"✅ Оверрайд снят для ID {target}")
            elif val.isdigit() and 0 <= int(val) <= 9:
                image_policy.set_max_level_override(target, int(val))
                await event.respond(f"✅ Максимум для ID {target}: {val}/9")
            else:
                await event.respond("Значение: 0..9 или auto")
        else:
            await event.respond(
                "Использование:\n"
                "`/level <user_id> <0..9>`\n"
                "`/level <user_id> auto`"
            )
        return True

    if text == "/trusts":
        from lada.core.memory import list_users
        ids = list_users()
        if not ids:
            await event.respond("📭 Пользователей нет.")
        else:
            lines = ["🔐 **Уровни доверия:**"]
            for uid in ids:
                name = users_names.display_name(uid)
                t = image_policy.get_trust(uid)
                i = image_policy.get_intimacy(uid)
                f = familiarity(uid)
                lvl = image_policy._max_level_for_user(uid)
                eff = image_policy.effective_max_level(uid)
                ovr = image_policy.get_max_level_override(uid)
                st = get_status_label(uid)
                city = user_city.get_city(uid)
                city_str = f", city={city}" if city else ""
                ovr_str = f", override={ovr}" if ovr is not None else ""
                lines.append(f"• [{st}] {name}: trust={t:.2f}, int={i:.2f}, fam={f:.2f}, base={lvl}, eff={eff}{ovr_str}{city_str}")
            await event.respond("\n".join(lines))
        return True

    if text.startswith("/image"):
        prompt = text[len("/image"):].strip()
        if not prompt:
            await event.respond("Использование: `/image описание сцены`")
            return True

        allowed, reason = image_policy.check_allowed(owner_id, prompt, source="chat")
        if not allowed:
            await event.respond(f"🚫 Отказано: {reason}")
            return True

        await event.respond(f"🎨 Генерирую: _{prompt}_")
        img_path = await asyncio.to_thread(image_gen.generate, prompt)
        if img_path:
            try:
                await client.send_file(event.chat_id, img_path)
            except Exception as e:
                await event.respond(f"⚠️ Не удалось отправить: {e}")
        else:
            await event.respond("⚠️ Не удалось сгенерировать.")
        return True

    if text == "/stats":
        lines = [
            f"🧠 FAISS всего: {faiss_mem.count()}",
            f"   👁 наблюдения (о тебе): {faiss_mem.count_by_category('observation', owner_id)}",
            f"   💭 эмоции: {faiss_mem.count_by_category('emotion', owner_id)}",
            f"   🗣 монологи: {faiss_mem.count_by_category('monologue', None)}",
            f"   🪞 рефлексии: {faiss_mem.count_by_category('reflection', None)}",
            f"💭 Настроение: {mood.get_mood()}",
            f"📬 Очередь: {message_queue.size()}",
        ]
        await event.respond("\n".join(lines))
        return True

    return False


# ─────────────── Обработка батча ───────────────

async def process_batch(batch: list[QueuedMessage]):
    if not batch:
        return

    user_id = batch[0].user_id
    chat_id = batch[0].chat_id
    user_display = users_names.display_name(user_id)

    log(f"📥 Из очереди: {len(batch)} сообщ. от {user_display} (осталось: {message_queue.size()})")

    touch_activity()
    await set_online(client)
    try:
        await client.send_read_acknowledge(chat_id)
    except Exception as e:
        log(f"⚠️ Read: {e}")

    parts: list[str] = []
    for qm in batch:
        text = (qm.event.raw_text or "").strip()
        if text:
            parts.append(text)
        if qm.event.media:
            try:
                media_desc = await process_media(qm.event.message)
                if media_desc:
                    parts.append(media_desc)
            except Exception as e:
                log(f"⚠️ Медиа: {e}")

    combined = "\n\n".join(parts).strip()
    if not combined:
        return

    # Автоопределение города
    if not user_city.get_city(user_id):
        city_match = re.search(
            r'(?:я\s+из|живу\s+в|нахожусь\s+в|пишу\s+из)\s+([А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)?)',
            combined,
        )
        if city_match:
            detected = city_match.group(1).strip()
            user_city.set_city(user_id, detected)
            log(f"   🏙 Автоопределён город: {detected}")

    pending = get_pending(user_id)
    if pending:
        answer = _detect_yes_no(combined)
        if answer == "yes":
            result = accept_proposal(user_id)
            if result:
                log(f"   💗 Предложение '{result}' ПРИНЯТО")
                add_message(
                    user_id, "system",
                    f"[СИСТЕМА] Пользователь согласился стать твоим "
                    f"{'мужем' if result == 'husband' else 'любовником'}.",
                    max_history=MAX_HISTORY,
                )
        elif answer == "no":
            result = reject_proposal(user_id)
            if result:
                log(f"   💔 Предложение '{result}' ОТКЛОНЕНО")
                add_message(
                    user_id, "system",
                    f"[СИСТЕМА] Пользователь отказался стать твоим "
                    f"{'мужем' if result == 'husband' else 'любовником'}. "
                    f"Тебе больно. Кулдаун месяц.",
                    max_history=MAX_HISTORY,
                )
        else:
            log(f"   ⏳ Предложение '{pending}' ожидает, ответ неоднозначный")

    add_message(user_id, "user", combined, max_history=MAX_HISTORY)
    history = get_history(user_id, limit=MAX_HISTORY)

    full_history_count = len(get_history(user_id))

    candidates = faiss_mem.search(
        combined,
        k=FAISS_TOP_K,
        user_id=user_id,
        min_score=FAISS_MIN_SCORE,
        max_chars=FAISS_MAX_CHARS,
    )
    related = [r for r in candidates if r.get("category") != "emotion"]
    if related:
        knowledge_text = "\n".join(f"- {k['text']}" for k in related)
        history = history + [{
            "role": "system",
            "content": (
                f"Вот что ты помнишь (про этого пользователя и в целом):\n"
                f"{knowledge_text}"
            )
        }]
        log(f"   🧠 FAISS: {len(related)} записей, {len(knowledge_text)} симв.")
    else:
        log(f"   🧠 FAISS: пусто")

    IMAGE_TRIGGERS = [
        "селфи", "selfie", "покажи себя", "покажись",
        "пришли фото", "пришли фотку", "покажи фото",
        "как ты выглядишь", "сфоткайся", "сделай фото",
        "сделай селфи", "покажи как ты", "где ты сейчас",
        "покажи мусю", "покажи кошку", "фото кота", "фото кошки",
    ]
    user_requested_photo = any(t in combined.lower() for t in IMAGE_TRIGGERS)

    SEARCH_TRIGGERS = [
        "поищи", "погугли", "загугли", "найди в интернете",
        "проверь в интернете", "посмотри в интернете",
        "что в интернете", "найди информацию", "поиск в интернете",
    ]
    force_search = WEB_SEARCH_ENABLED and any(
        trigger in combined.lower() for trigger in SEARCH_TRIGGERS
    )

    user_explicit_nsfw = image_policy.is_explicit_nsfw_request(combined)

    brief = build_state_brief(
        user_id, combined, user_display,
        current_msg_count=full_history_count,
        user_requested_photo=user_requested_photo,
    )
    history = history + [{"role": "system", "content": brief}]

    thoughts = get_recent_thoughts()
    if thoughts:
        thoughts_text = "\n".join(f"- {t['text'][:150]}" for t in thoughts)
        history = history + [{
            "role": "system",
            "content": (
                "Недавно ты думала про себя:\n"
                f"{thoughts_text}\n\n"
                "(Это твои личные мысли. Не упоминай их напрямую.)"
            )
        }]

    reply = ""
    thinking = ""
    round_num = 0

    async with client.action(chat_id, "typing"):
        if force_search:
            log(f"   🔍 Принудительный поиск: {combined[:80]}")
            search_result = await asyncio.to_thread(
                web_search.search_and_format, combined
            )
            history = history + [
                {"role": "system", "content": search_result},
                {"role": "system",
                 "content": "Пользователь попросил найти информацию. Используй результаты."},
            ]

        while round_num <= WEB_SEARCH_MAX_ROUNDS:
            reply, thinking = await asyncio.to_thread(ask, history)

            if WEB_SEARCH_ENABLED and "[SEARCH]" in reply and not force_search:
                match = re.search(r'\[SEARCH\]\s*(.+)', reply)
                if match:
                    query = match.group(1).strip()
                    if query:
                        log(f"   🔍 Веб-поиск: {query}")
                        search_result = await asyncio.to_thread(
                            web_search.search_and_format, query
                        )
                        history = history + [
                            {"role": "assistant", "content": reply},
                            {"role": "system", "content": search_result},
                            {"role": "system",
                             "content": "Теперь дай финальный ответ. Не пиши [SEARCH] снова."},
                        ]
                        round_num += 1
                        continue
            break

    log_thinking(thinking)

    if "[SEARCH]" in reply:
        reply = "🤔 Не нашла точный ответ."

    # ─── [SILENCE] ───
    if "[SILENCE]" in reply:
        log(f"   🤐 Модель решила промолчать.")
        add_message(user_id, "assistant", "[SILENCE]", max_history=MAX_HISTORY)
        return

    # ─── [PROPOSE:...] ───
    propose_match = re.search(r'\[PROPOSE:\s*(lover|husband)\s*\]', reply)
    if propose_match and not get_pending(user_id) and get_status(user_id) not in rel.SPECIAL_STATUSES:
        which = propose_match.group(1)
        record_proposal(user_id, which)
        reply = reply.replace(propose_match.group(0), "").strip()
        log(f"   💗 Модель предложила отношения: {which}")

    # ─── [STICKER] / [STICKER:emoji] ───
    sticker_match = re.search(r'\[STICKER(?::\s*([^\]]+))?\]', reply)
    sticker_emoji = None
    need_sticker = False

    if sticker_match:
        need_sticker = True
        sticker_emoji = sticker_match.group(1)
        if sticker_emoji:
            sticker_emoji = sticker_emoji.strip()
        reply = reply.replace(sticker_match.group(0), "").strip()
        log(f"   🎭 Модель хочет стикер (emoji={sticker_emoji or 'по настроению'})")

    # ─── [IMAGE] ───
    image_hint = None
    image_subject = "lada"

    match = re.search(r'\[IMAGE\]\s*pet:\s*(.+)', reply)
    if match:
        image_hint = match.group(1).strip()
        image_subject = "pet"
        reply = reply.replace(match.group(0), "").strip()
    else:
        match = re.search(r'\[IMAGE\]\s*(?:hint:\s*)?(.+)', reply)
        if match:
            image_hint = match.group(1).strip()
            reply = reply.replace(match.group(0), "").strip()

    if not image_hint and user_requested_photo:
        log(f"   ℹ️ Пользователь просил фото, модель не написала [IMAGE] — отказ")

    image_prompt = None
    if image_hint:
        if image_subject == "pet":
            image_prompt = image_hint
        else:
            image_prompt = image_mood.build_scene(
                image_hint, mood.get_mood(), user_id, _recent_photo_prompts
            )

    if image_prompt and image_subject == "lada":
        allowed, reason = image_policy.check_allowed(
            user_id, image_prompt,
            source="chat",
            user_explicit_nsfw=user_explicit_nsfw,
        )
        log(f"   🔒 Политика: {image_policy.debug_state(user_id, image_prompt)}")
        if not allowed:
            log(f"   🚫 Отказ политики: {reason}")
            image_prompt = None
            if not reply.strip():
                reply = image_policy.explain_refusal(user_id, image_prompt)

    if not reply.strip() and not need_sticker:
        if image_prompt:
            reply = "Сейчас, секунду."
        else:
            log("   ⚠️ Пустой ответ, прошу заново")
            history_retry = history + [
                {"role": "assistant", "content": reply},
                {"role": "system",
                 "content": "Ты не написала ответ. Напиши короткий ответ пользователю."},
            ]
            reply = await asyncio.to_thread(ask_text, history_retry)
            if not reply.strip():
                reply = "⚠️ Что-то у меня сбой. Повтори."

    if image_prompt:
        log(f"   🎨 Итоговый промпт ({image_subject}): {image_prompt[:140]}")

    add_message(user_id, "assistant", reply, max_history=MAX_HISTORY)

    # ─── Стикер: отправляем ДО текста, как эмоциональный акцент ───
    if need_sticker:
        try:
            sent = await stickers.send_sticker_by_mood(
                client, chat_id,
                mood=mood.get_mood(),
                force_emoji=sticker_emoji,
            )
            if sent:
                log(f"   🎭 Стикер отправлен (mood={mood.get_mood()})")
        except Exception as e:
            log(f"   ⚠️ Стикер не ушёл: {e}")

    # ─── Отправка текста ───
    if reply.strip():
        try:
            sent_msgs = await splitter.send_in_parts(client, chat_id, reply)
            log(f"✅ Ответил ({user_display}, частей: {len(sent_msgs)})")
        except Exception as e:
            log(f"⚠️ Не удалось отправить: {e}")

    # ─── Генерация фото ───
    if image_prompt:
        log(f"   🎨 Генерация ({image_subject}): {image_prompt[:100]}...")

        if image_subject == "lada":
            extra_neg = image_mood.get_negative_for_mood(mood.get_mood(), user_id)
            neg = image_gen.BASE_NEGATIVE + (", " + extra_neg if extra_neg else "")
        else:
            neg = image_gen.PET_NEGATIVE

        async with client.action(chat_id, "photo"):
            img_path = await asyncio.to_thread(
                partial(
                    image_gen.generate,
                    image_prompt,
                    negative_prompt=neg,
                    subject=image_subject,
                )
            )

        if img_path:
            try:
                await client.send_file(chat_id, img_path)
                log(f"   🖼 Изображение отправлено ({image_subject})")
                _recent_photo_prompts.append(image_prompt)
                if len(_recent_photo_prompts) > 10:
                    _recent_photo_prompts.pop(0)
                will.touch_photo_sent(
                    user_id,
                    len(get_history(user_id)),
                    image_prompt,
                )
                faiss_mem.add(
                    f"[Сгенерировала фото] {image_prompt}",
                    category="image",
                    source="image_gen",
                    user_id=user_id,
                )
            except Exception as e:
                log(f"   ⚠️ Не удалось отправить изображение: {e}")

    if EMOTION_ENABLED and not image_prompt:
        asyncio.create_task(evaluate_and_record(combined, reply, user_id))
    elif image_prompt:
        log("   ⏭ Эмоции пропущены (VRAM занят после генерации)")


# ─────────────── Приём сообщений ───────────────

@client.on(events.NewMessage(incoming=True))
async def handler(event):
    user_id = event.sender_id
    text = event.raw_text or ""

    touch_user_activity(user_id)
    touch_first_seen(user_id)

    if user_id == OWNER_ID and text.startswith("/"):
        try:
            await client.send_read_acknowledge(event.chat_id)
        except Exception:
            pass
        if await handle_owner_command(text, event):
            return

    if not should_reply(user_id, OWNER_ID):
        return

    if not text and not event.media:
        return

    if user_id != OWNER_ID:
        allowed, wait = is_allowed(user_id, COOLDOWN_SECONDS)
        if not allowed:
            log(f"⏸ Cooldown: {users_names.display_name(user_id)}, ждать {wait}с")
            return
        touch(user_id)

    try:
        sender = await event.get_sender()
        users_names.update(
            user_id,
            username=getattr(sender, "username", "") or "",
            first_name=getattr(sender, "first_name", "") or "",
            last_name=getattr(sender, "last_name", "") or "",
            phone=getattr(sender, "phone", "") or "",
        )
    except Exception as e:
        log(f"⚠️ Не удалось получить данные отправителя {user_id}: {e}")

    user_display = users_names.display_name(user_id)

    qm = QueuedMessage(
        user_id=user_id,
        chat_id=event.chat_id,
        event=event,
        text=(text or "")[:300],
        username=user_display,
    )
    ok = await message_queue.put(qm)
    if ok:
        log(f"📬 +в очередь от {user_display} (всего: {message_queue.size()})")
    else:
        log(f"⚠️ Очередь переполнена, сообщение от {user_display} потеряно")


async def main():
    log("⏳ Подключаюсь к Telegram...")
    await client.start()
    me = await client.get_me()
    log(f"✅ Логин: {me.first_name} (@{me.username})")
    log(f"⚙️ Режим: {get_mode()}")
    log(f"📜 Персона: {len(get_persona())} символов")
    log(f"🧠 FAISS: {faiss_mem.count()} записей")
    log(f"💭 Настроение: {mood.get_mood()}")
    log(f"👥 Пользователей в памяти: {len(faiss_mem.list_users())}")

    if image_policy.get_trust(OWNER_ID) == 0 and image_policy.get_intimacy(OWNER_ID) == 0:
        image_policy.set_trust(OWNER_ID, trust=1.0, intimacy=1.0)
        log(f"🔐 Владелец: trust=1.0, intimacy=1.0 (стартовые значения)")

    message_queue.set_handler(process_batch)
    await message_queue.start()
    log(f"📬 Очередь: воркеров {QUEUE_WORKER_COUNT}, debounce {QUEUE_DEBOUNCE_SECS}с")

    asyncio.create_task(reflection_loop(REFLECTION_INTERVAL))
    log(f"🧠 Саморефлексия: каждые {REFLECTION_INTERVAL // 60} мин")

    asyncio.create_task(sleep_loop())
    log(f"💤 Сон: первый через 1 ч")

    asyncio.create_task(monologue_loop())
    log(f"💭 Монолог: первая мысль через {MONOLOGUE_FIRST_DELAY_MINUTES} мин")

    asyncio.create_task(presence_loop(client, idle_minutes=IDLE_MINUTES))
    log(f"🟢 Статус-контроль: offline через {IDLE_MINUTES} мин")

    asyncio.create_task(sins_regression_loop())
    log(f"⚖️ Регрессия шкал: раз в час")

    if INITIATIVE_ENABLED:
        asyncio.create_task(initiative_loop(client))
        log(f"💌 Инициатива: проверка каждые {INITIATIVE_CHECK_INTERVAL_MINUTES} мин")

    asyncio.create_task(start_web())
    log(f"🌐 Веб-панель: http://localhost:{WEB_PORT}")

    log("👂 Слушаю сообщения...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())