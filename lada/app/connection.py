# connection.py — failover подключения к Telegram через MTProxy или напрямую

import asyncio
import socket as _socket

from telethon import TelegramClient, connection

from config import (
    API_ID, API_HASH,
    TELEGRAM_PROXIES, TELEGRAM_ALLOW_DIRECT, TELEGRAM_PROXY_CHECK_TIMEOUT,
)
from lada.app.utils import log


def _tcp_alive(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def _iter_connection_variants():
    for i, proxy in enumerate(TELEGRAM_PROXIES):
        host, port, secret = proxy
        if not _tcp_alive(host, port, TELEGRAM_PROXY_CHECK_TIMEOUT):
            log(f"⚠️  Прокси #{i} недоступен по TCP ({host}:{port})")
            continue
        log(f"🔎 Прокси #{i} жив по TCP, пробую MTProto ({host}:{port})")
        yield i, proxy

    if TELEGRAM_ALLOW_DIRECT:
        log("⚠️  Все прокси не подошли, пробую прямое подключение")
        yield -1, None


def _create_client(proxy):
    if proxy is None:
        return TelegramClient("lada_session", API_ID, API_HASH)
    return TelegramClient(
        "lada_session", API_ID, API_HASH,
        connection=connection.ConnectionTcpMTProxyAbridged,
        proxy=proxy,
    )


async def connect_with_failover() -> TelegramClient | None:
    for idx, proxy in _iter_connection_variants():
        cli = None
        try:
            cli = _create_client(proxy)
            log(f"⏳ Подключение (вариант #{idx})...")

            try:
                await asyncio.wait_for(cli.connect(), timeout=15)
            except asyncio.TimeoutError:
                log(f"⏰ Таймаут (вариант #{idx})")
                try:
                    await cli.disconnect()
                except Exception:
                    pass
                continue

            if await cli.is_user_authorized():
                if idx < 0:
                    log("✅ Подключился напрямую (без прокси)")
                else:
                    log(f"✅ Подключился через прокси #{idx}")
                return cli
            else:
                await cli.disconnect()
                log(f"❌ Сессия не авторизована (вариант #{idx})")
                return None
        except Exception as e:
            log(f"❌ Вариант #{idx} упал: {type(e).__name__}: {e}")
            if cli is not None:
                try:
                    await cli.disconnect()
                except Exception:
                    pass
            continue
    return None