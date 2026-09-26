# presence.py — управление статусом online/offline и активностью

import asyncio
from datetime import datetime
from telethon.tl.functions.account import UpdateStatusRequest

_last_activity: datetime | None = None


def touch_activity():
    """Отмечает свежую активность."""
    global _last_activity
    _last_activity = datetime.now()


def get_last_activity() -> datetime | None:
    return _last_activity


async def set_online(client):
    """Переводит аккаунт в режим online."""
    try:
        await client(UpdateStatusRequest(offline=False))
    except Exception as e:
        print(f"⚠️ Не удалось установить online: {e}")


async def set_offline(client):
    """Переводит аккаунт в режим offline (last seen)."""
    try:
        await client(UpdateStatusRequest(offline=True))
    except Exception as e:
        print(f"⚠️ Не удалось установить offline: {e}")


async def presence_loop(client, idle_minutes: int = 5):
    """
    Каждую минуту проверяет, была ли активность.
    Если давно не было — переводит в offline.
    Когда придёт сообщение — main.py вызовет set_online().
    """
    global _last_activity
    _last_activity = datetime.now()
    await set_online(client)

    while True:
        await asyncio.sleep(60)
        if _last_activity is None:
            continue
        delta = (datetime.now() - _last_activity).total_seconds()
        if delta > idle_minutes * 60:
            # если уже offline — не спамим запросами
            if delta < (idle_minutes * 60) + 90:
                await set_offline(client)
                print(f"🌙 Ушла в offline (простой {int(delta/60)} мин)")