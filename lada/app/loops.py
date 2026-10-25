# loops.py — фоновые задачи: веб-сервер, регрессия шкал и настроения,
# self-consistency Лады.

import asyncio

import uvicorn

from config import (
    WEB_PORT,
    SELF_CONSISTENCY_ENABLED,
    SELF_CONSISTENCY_INTERVAL_HOURS,
)
from lada.brain import sins, mood
from lada.app.utils import log


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
            mood.regress_if_stale()
            # C4: если негатив держится долго — плавно отпускаем
            try:
                from lada.brain import emotional_inertia
                emotional_inertia.regress_if_held()
            except Exception as e:
                log(f"⚠️ inertia regression: {e}")
        except Exception as e:
            log(f"⚠️ Ошибка регрессии шкал: {e}")


async def self_consistency_loop():
    """Раз в сутки (или при первом ответе после суток) обновляем
    портрет позиций Лады."""
    if not SELF_CONSISTENCY_ENABLED:
        print("   ⏸ self_consistency выключен")
        return

    # ждём 5 минут после старта, чтобы не мешать инициализации
    await asyncio.sleep(5 * 60)

    while True:
        try:
            from lada.brain import self_consistency
            ok = await self_consistency.rebuild()
            if ok:
                log("🪞 Портрет позиций Лады обновлён")
            else:
                log("🪞 self_consistency: пока нечего анализировать")
        except Exception as e:
            log(f"⚠️ self_consistency: {e}")

        await asyncio.sleep(SELF_CONSISTENCY_INTERVAL_HOURS * 3600)