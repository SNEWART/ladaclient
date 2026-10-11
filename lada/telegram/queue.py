# queue.py — очередь сообщений с учётом печати пользователя.
#
# FIX: после debounce проверяем, печатает ли пользователь.
# Если да — продлеваем ожидание. Максимум MAX_TYPING_EXTENSIONS раз.

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from lada.app import typing_tracker


MAX_TYPING_EXTENSIONS = 5
TYPING_EXTENSION_WINDOW = 3.0


@dataclass
class QueuedMessage:
    user_id: int
    chat_id: int
    event: object
    text: str = ""
    username: str = ""
    enqueued_at: float = field(default_factory=time.time)


class MessageQueue:
    def __init__(self, worker_count=1, max_size=1000, debounce_secs=1.5):
        self.worker_count = worker_count
        self.max_size = max_size
        self.debounce_secs = debounce_secs
        self._items = []
        self._cond = asyncio.Condition()
        self._workers = []
        self._handler = None
        self._running = False

    def set_handler(self, handler):
        self._handler = handler

    def size(self) -> int:
        return len(self._items)

    def snapshot(self) -> list:
        now = time.time()
        out = []
        for i, m in enumerate(self._items, 1):
            out.append({
                "index": i,
                "user_id": m.user_id,
                "username": m.username or str(m.user_id),
                "text": m.text[:300],
                "enqueued_at": datetime.fromtimestamp(m.enqueued_at).strftime("%H:%M:%S"),
                "wait_seconds": int(now - m.enqueued_at),
            })
        return out

    async def put(self, msg: QueuedMessage) -> bool:
        async with self._cond:
            if len(self._items) >= self.max_size:
                return False
            self._items.append(msg)
            self._cond.notify()
            return True

    async def remove_at(self, index: int) -> bool:
        async with self._cond:
            if 1 <= index <= len(self._items):
                self._items.pop(index - 1)
                return True
            return False

    async def clear(self) -> int:
        async with self._cond:
            n = len(self._items)
            self._items.clear()
            return n

    async def start(self):
        if self._running:
            return
        self._running = True
        for i in range(self.worker_count):
            self._workers.append(asyncio.create_task(self._worker(i)))

    async def stop(self):
        self._running = False
        async with self._cond:
            self._cond.notify_all()
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    async def _wait_for_typing_stop(self, user_id: int):
        """
        Если пользователь печатает — ждём. Продлеваем debounce, пока
        он не остановится. Не больше MAX_TYPING_EXTENSIONS раз.
        """
        extensions = 0
        while extensions < MAX_TYPING_EXTENSIONS:
            if not typing_tracker.is_typing(user_id, TYPING_EXTENSION_WINDOW):
                return
            print(f"   ⌨️  {user_id} печатает, жду ещё {self.debounce_secs}с")
            await asyncio.sleep(self.debounce_secs)
            extensions += 1

    async def _pop_batch(self) -> list:
        async with self._cond:
            while self._running and not self._items:
                await self._cond.wait()
            if not self._items:
                return []
            first = self._items.pop(0)

        # Базовый debounce
        if self.debounce_secs > 0:
            await asyncio.sleep(self.debounce_secs)

        # Дополнительно ждём, если он печатает следующее
        await self._wait_for_typing_stop(first.user_id)

        batch = [first]
        async with self._cond:
            i = 0
            while i < len(self._items):
                if self._items[i].user_id == first.user_id:
                    batch.append(self._items.pop(i))
                else:
                    i += 1
        return batch

    async def _worker(self, idx: int):
        while self._running:
            try:
                batch = await self._pop_batch()
                if not batch:
                    continue
                if self._handler:
                    await self._handler(batch)
            except asyncio.CancelledError:
                return
            except Exception as e:
                print(f"⚠️ Worker {idx}: {e}")