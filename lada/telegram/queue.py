# queue.py — очередь сообщений с возможностью удаления

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable


@dataclass
class QueuedMessage:
    user_id: int
    chat_id: int
    event: object
    text: str = ""
    username: str = ""
    enqueued_at: float = field(default_factory=time.time)


class MessageQueue:
    """
    Очередь сообщений на list + asyncio.Condition.
    Поддерживает удаление по индексу и просмотр состояния (для UI).
    Пока сообщение в очереди — оно не прочитано в Telegram.
    """

    def __init__(
        self,
        worker_count: int = 1,
        max_size: int = 1000,
        debounce_secs: float = 1.5,
    ):
        self.worker_count = worker_count
        self.max_size = max_size
        self.debounce_secs = debounce_secs

        self._items: list[QueuedMessage] = []
        self._cond = asyncio.Condition()
        self._workers: list[asyncio.Task] = []
        self._handler: Callable[[list[QueuedMessage]], Awaitable[None]] | None = None
        self._running = False

    # ─────────────── API для main.py ───────────────

    def set_handler(self, handler):
        self._handler = handler

    def size(self) -> int:
        return len(self._items)

    def snapshot(self) -> list[dict]:
        """Сериализуемое представление очереди для UI."""
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
        """Удалить по индексу (1-based)."""
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

    # ─────────────── Воркеры ───────────────

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

    async def _pop_batch(self) -> list[QueuedMessage]:
        """Забирает первое сообщение и все последующие от того же юзера."""
        async with self._cond:
            while self._running and not self._items:
                await self._cond.wait()
            if not self._items:
                return []
            first = self._items.pop(0)

        # debounce вне лока, чтобы не блокировать put/remove
        if self.debounce_secs > 0:
            await asyncio.sleep(self.debounce_secs)

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