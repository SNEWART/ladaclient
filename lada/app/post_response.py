# post_response.py — оставляем только companion_style (раз в 8)
# и promise_tracker (раз в 2). Всё остальное ушло в unified_analysis.

import asyncio

from config import PROMISE_CHECK_EVERY_N
from lada.app.utils import log


_reply_counter: dict = {}


def _bump(user_id: int) -> int:
    _reply_counter[user_id] = _reply_counter.get(user_id, 0) + 1
    return _reply_counter[user_id]


async def run(user_id: int, user_msg: str, bot_reply: str):
    n = _bump(user_id)

    # companion_style — раз в 8 ответов (тяжёлый анализ лексики)
    try:
        from lada.brain import companion_style
        await companion_style.update_if_needed(user_id)
    except Exception as e:
        log(f"   ⚠️ companion_style: {e}")

    # promise_tracker — раз в N
    if bot_reply and n % max(PROMISE_CHECK_EVERY_N, 1) == 0:
        try:
            from lada.brain import promise_tracker
            await promise_tracker.check_reply(user_id, bot_reply)
        except Exception as e:
            log(f"   ⚠️ promise_tracker: {e}")