# loops.py — фоновые задачи: веб-сервер, регрессия шкал

import asyncio

import uvicorn

from config import WEB_PORT
from lada.brain import sins
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
        except Exception as e:
            log(f"⚠️ Ошибка регрессии шкал: {e}")