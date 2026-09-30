# main.py — тонкая точка входа. Вся логика в lada/app/

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import asyncio

from telethon import events

from config import (
    OWNER_ID, SHOW_RAW_EVENTS, REFLECTION_INTERVAL,
    IDLE_MINUTES, MONOLOGUE_FIRST_DELAY_MINUTES,
    INITIATIVE_ENABLED, INITIATIVE_CHECK_INTERVAL_MINUTES,
    WEB_PORT, QUEUE_WORKER_COUNT, QUEUE_DEBOUNCE_SECS,
)
from lada.core.persona import get_persona
from lada.core.faiss_memory import faiss_mem
from lada.brain import mood, image_policy
from lada.brain.reflection import reflection_loop
from lada.brain.initiative import initiative_loop
from lada.brain.sleep import sleep_loop
from lada.brain.monologue import monologue_loop
from lada.telegram.presence import presence_loop
from lada.telegram.mode import get_mode
from lada.app.client import set_client, message_queue
from lada.app.connection import connect_with_failover
from lada.app.handlers import handler, process_batch
from lada.app.loops import start_web, sins_regression_loop
from lada.app.utils import log


NOISY_EVENTS = {
    "UpdateUserStatus", "UpdateUserTyping", "UpdateChatUserTyping",
    "UpdateChatParticipants", "UpdateReadHistoryInbox", "UpdateReadHistoryOutbox",
    "UpdateReadMessagesContents", "UpdateChannelReadMessagesContents",
    "UpdatePts", "UpdateNewScheduledMessage", "UpdateMessageID",
    "UpdateMessagePollVote", "UpdateChatOnlineMembers",
}


async def main():
    log("⏳ Подключаюсь к Telegram...")

    client = await connect_with_failover()
    if client is None:
        log("💀 Не удалось подключиться ни одним способом. Выход.")
        _sys.exit(1)

    set_client(client)
    client.add_event_handler(handler)

    if SHOW_RAW_EVENTS:
        @client.on(events.Raw())
        async def raw_handler(update):
            name = type(update).__name__
            if name in NOISY_EVENTS:
                return
            print(f"   · {name}", flush=True)

    me = await client.get_me()
    log(f"✅ Логин: {me.first_name} (@{me.username})")
    log(f"⚙️ Режим: {get_mode()}")
    log(f"📜 Персона: {len(get_persona())} символов")
    log(f"🧠 FAISS: {faiss_mem.count()} записей")
    log(f"💭 Настроение: {mood.get_mood()}")
    log(f"👥 Пользователей в памяти: {len(faiss_mem.list_users())}")

    if image_policy.get_trust(OWNER_ID) == 0 and image_policy.get_intimacy(OWNER_ID) == 0:
        image_policy.set_trust(OWNER_ID, trust=1.0, intimacy=1.0)
        log("🔐 Владелец: trust=1.0, intimacy=1.0 (стартовые значения)")

    message_queue.set_handler(process_batch)
    await message_queue.start()
    log(f"📬 Очередь: воркеров {QUEUE_WORKER_COUNT}, debounce {QUEUE_DEBOUNCE_SECS}с")

    asyncio.create_task(reflection_loop(REFLECTION_INTERVAL))
    log(f"🧠 Саморефлексия: каждые {REFLECTION_INTERVAL // 60} мин")

    asyncio.create_task(sleep_loop())
    log("💤 Сон: первый через 1 ч")

    asyncio.create_task(monologue_loop())
    log(f"💭 Монолог: первая мысль через {MONOLOGUE_FIRST_DELAY_MINUTES} мин")

    asyncio.create_task(presence_loop(client, idle_minutes=IDLE_MINUTES))
    log(f"🟢 Статус-контроль: offline через {IDLE_MINUTES} мин")

    asyncio.create_task(sins_regression_loop())
    log("⚖️ Регрессия шкал: раз в час")

    if INITIATIVE_ENABLED:
        asyncio.create_task(initiative_loop(client))
        log(f"💌 Инициатива: проверка каждые {INITIATIVE_CHECK_INTERVAL_MINUTES} мин")

    asyncio.create_task(start_web())
    log(f"🌐 Веб-панель: http://localhost:{WEB_PORT}")

    log("👂 Слушаю сообщения...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())