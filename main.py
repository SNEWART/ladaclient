# main.py — точка входа. С SQLite + догоном пропущенных сообщений.

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import asyncio
import socket
from datetime import datetime, timedelta

from telethon import events

from config import (
    OWNER_ID, SHOW_RAW_EVENTS, REFLECTION_INTERVAL,
    IDLE_MINUTES, MONOLOGUE_FIRST_DELAY_MINUTES,
    INITIATIVE_ENABLED, INITIATIVE_CHECK_INTERVAL_MINUTES,
    WEB_PORT, QUEUE_WORKER_COUNT, QUEUE_DEBOUNCE_SECS,
    MAX_HISTORY,
    MEMORY_REACTOR_INTERVAL_MINUTES,
    SELF_CONSISTENCY_INTERVAL_HOURS,
    LIFE_LOG_INTERVAL_HOURS,
    EMOTIONAL_PEAKS_INTERVAL_HOURS,
)
from lada.core.db import init_db, close_conn
from lada.core.persona import get_persona
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core.memory import add_message, get_history, list_users
from lada.brain import mood, image_policy
from lada.brain.reflection import reflection_loop
from lada.brain.initiative import initiative_loop
from lada.brain.sleep import sleep_loop
from lada.brain.monologue import monologue_loop
from lada.brain.return_loop import return_loop
from lada.brain.activity import activity_loop
from lada.telegram.presence import presence_loop
from lada.telegram.mode import get_mode
from lada.telegram.queue import QueuedMessage
from lada.app.client import set_client, message_queue
from lada.app.connection import connect_with_failover
from lada.app.handlers import handler, process_batch
from lada.app.loops import (
    start_web, sins_regression_loop, self_consistency_loop,
)
from lada.app.utils import log


NOISY_EVENTS = {
    "UpdateUserStatus", "UpdateUserTyping", "UpdateChatUserTyping",
    "UpdateChatParticipants", "UpdateReadHistoryInbox", "UpdateReadHistoryOutbox",
    "UpdateReadMessagesContents", "UpdateChannelReadMessagesContents",
    "UpdatePts", "UpdateNewScheduledMessage", "UpdateMessageID",
    "UpdateMessagePollVote", "UpdateChatOnlineMembers",
}

CATCHUP_LIMIT = 10
CATCHUP_MAX_AGE_HOURS = 24


def _silent_event_loop_handler(loop, context):
    exc = context.get("exception")
    msg = context.get("message", "")
    if isinstance(exc, ConnectionResetError):
        return
    if "Accept failed" in msg:
        return
    if "_call_connection_lost" in msg:
        return
    if "Unsupported upgrade request" in msg:
        return
    loop.default_exception_handler(context)


async def catch_up_missed(client):
    log("🔄 Проверяю пропущенные сообщения...")

    try:
        dialogs = await client.get_dialogs(limit=50)
    except Exception as e:
        log(f"⚠️ Не удалось получить диалоги: {e}")
        return

    me = await client.get_me()
    me_id = me.id
    cutoff = datetime.now().astimezone() - timedelta(hours=CATCHUP_MAX_AGE_HOURS)

    caught = 0

    for dialog in dialogs:
        try:
            if not dialog.is_user:
                continue
            peer = dialog.entity
            peer_id = peer.id
            if peer_id == me_id:
                continue

            messages = await client.get_messages(peer_id, limit=CATCHUP_LIMIT)
            if not messages:
                continue

            messages = list(reversed(messages))

            last_our_reply_idx = -1
            for i, m in enumerate(messages):
                if m.out and not m.action:
                    last_our_reply_idx = i

            missed = []
            for m in messages[last_our_reply_idx + 1:]:
                if m.out or m.action:
                    continue
                msg_date = m.date.astimezone()
                if msg_date < cutoff:
                    continue
                missed.append(m)

            if not missed:
                continue

            user_hist = get_history(peer_id, limit=20)
            hist_texts = {
                (h.get("content") or "").strip()
                for h in user_hist if h.get("role") == "user"
            }

            fresh = [
                m for m in missed
                if (m.raw_text or "").strip() and
                   (m.raw_text or "").strip() not in hist_texts
            ]

            if not fresh:
                continue

            text_parts = [m.raw_text.strip() for m in fresh if m.raw_text]
            text = "\n\n".join(text_parts).strip()

            if not text:
                continue

            fake = _FakeEvent(client, peer_id, fresh[-1])
            qm = QueuedMessage(
                user_id=peer_id,
                chat_id=peer_id,
                event=fake,
                text=text[:300],
                username=getattr(peer, "first_name", "") or str(peer_id),
            )
            ok = await message_queue.put(qm)
            if ok:
                caught += len(fresh)
                log(f"   ↩️  Догнал {len(fresh)} сообщ. от "
                    f"{qm.username} (ID {peer_id})")

        except Exception as e:
            log(f"⚠️ Ошибка догона для {dialog.id}: {e}")
            continue

    if caught:
        log(f"🔄 Всего догнано: {caught} сообщ.")
    else:
        log("✅ Пропущенных нет")


class _FakeEvent:
    def __init__(self, client, chat_id, msg):
        self._client = client
        self.chat_id = chat_id
        self.message = msg
        self.raw_text = msg.raw_text or ""
        self.media = msg.media
        self.sender_id = chat_id

    async def get_sender(self):
        try:
            return await self._client.get_entity(self.chat_id)
        except Exception:
            return None

    async def respond(self, text, **kwargs):
        return await self._client.send_message(self.chat_id, text, **kwargs)


async def main():
    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_silent_event_loop_handler)
    except Exception:
        pass

    init_db()
    log("💾 SQLite: база инициализирована")

    log("⏳ Подключаюсь к Telegram...")

    client = await connect_with_failover()
    if client is None:
        log("💀 Не удалось подключиться ни одним способом. Выход.")
        close_conn()
        _sys.exit(1)

    from lada.app import typing_tracker
    from telethon.tl.types import UpdateUserTyping

    @client.on(events.Raw(types=UpdateUserTyping))
    async def typing_handler(update):
        uid = getattr(update, "user_id", None)
        if uid:
            typing_tracker.touch(uid)

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
    log(f"👥 Пользователей в памяти: {len(list_users())}")

    message_queue.set_handler(process_batch)
    await message_queue.start()
    log(f"📬 Очередь: воркеров {QUEUE_WORKER_COUNT}, debounce {QUEUE_DEBOUNCE_SECS}с")

    asyncio.create_task(catch_up_missed(client))

    asyncio.create_task(reflection_loop(REFLECTION_INTERVAL))
    log(f"🧠 Саморефлексия: каждые {REFLECTION_INTERVAL // 60} мин")

    asyncio.create_task(activity_loop())
    log("🎭 Занятие: обновление раз в 2 ч")

    asyncio.create_task(sleep_loop())
    log("💤 Сон: первый через 1 ч")

    asyncio.create_task(monologue_loop())
    log(f"💭 Монолог: первая мысль через {MONOLOGUE_FIRST_DELAY_MINUTES} мин")

    asyncio.create_task(presence_loop(client, idle_minutes=IDLE_MINUTES))
    log(f"🟢 Статус-контроль: offline через {IDLE_MINUTES} мин")

    asyncio.create_task(sins_regression_loop())
    log("⚖️ Регрессия шкал: раз в час")

    if INITIATIVE_ENABLED:
        from lada.brain.pending_loop import pending_loop
        asyncio.create_task(pending_loop(client))
        log("⏰ Отложенные инициативы: проверка раз в 5 мин")

        asyncio.create_task(initiative_loop(client))
        log(f"💌 Инициатива: каждые {INITIATIVE_CHECK_INTERVAL_MINUTES} мин")
        asyncio.create_task(return_loop(client))
        log("↩️  Возврат: каждые 30 мин")

    from lada.brain.memory_reactor import memory_reactor_loop
    asyncio.create_task(memory_reactor_loop())
    log(f"🧠 Реактор памяти: раз в {MEMORY_REACTOR_INTERVAL_MINUTES} мин")

    asyncio.create_task(self_consistency_loop())
    log(f"🪞 Портрет позиций: раз в {SELF_CONSISTENCY_INTERVAL_HOURS} ч")

    from lada.brain.life_log import life_loop
    asyncio.create_task(life_loop())
    log(f"🌱 Жизнь Лады: новое событие раз в {LIFE_LOG_INTERVAL_HOURS} ч")

    from lada.brain.emotional_peaks import peaks_loop
    asyncio.create_task(peaks_loop())
    log(f"💭 Эмоциональные пики: раз в {EMOTIONAL_PEAKS_INTERVAL_HOURS} ч")

    asyncio.create_task(start_web())
    log(f"🌐 Веб-панель: http://localhost:{WEB_PORT}")

    log("👂 Слушаю сообщения...")
    try:
        await client.run_until_disconnected()
    finally:
        close_conn()


if __name__ == "__main__":
    asyncio.run(main())