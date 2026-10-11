# typing_tracker.py — отслеживает, печатает ли пользователь.
#
# Хранит timestamp последнего UserTyping по каждому user_id.
# Очередь использует это, чтобы продлить debounce, пока пользователь
# ещё печатает следующее сообщение.

import time
import threading


_lock = threading.Lock()
_last_typing = {}


def touch(user_id: int):
    with _lock:
        _last_typing[user_id] = time.time()


def seconds_since_typing(user_id: int) -> float:
    with _lock:
        ts = _last_typing.get(user_id)
    if ts is None:
        return 999.0
    return time.time() - ts


def is_typing(user_id: int, window: float = 3.0) -> bool:
    return seconds_since_typing(user_id) < window


def clear(user_id: int):
    with _lock:
        _last_typing.pop(user_id, None)