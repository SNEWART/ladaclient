# login.py — одноразовый вход в Telegram через QR-код.
# Запуск: python login.py   (сам найдёт venv и перезапустится в нём)
#
# Создаёт lada_session.session в папке проекта.
# После успешного входа можно запускать run.bat.

import asyncio
import getpass
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.resolve()
SESSION_NAME = "lada_session"


# ─────────────────────────────── venv-перезапуск ───────────────────────────────

def _find_venv_python() -> Path | None:
    for rel in ("venv/Scripts/python.exe", ".venv/Scripts/python.exe"):
        p = PROJECT_ROOT / rel
        if p.exists():
            return p
    return None


def _reexec_in_venv_if_needed():
    """
    Если telethon не установлен в текущем интерпретаторе,
    но есть venv — перезапускаем скрипт через venv python.
    """
    try:
        import telethon  # noqa: F401
        return
    except ImportError:
        pass

    venv_py = _find_venv_python()
    if venv_py is None:
        print("❌ telethon не установлен, и venv не найден.")
        print("   Установи зависимости:")
        print("       pip install -r requirements.txt")
        sys.exit(1)

    if os.environ.get("LADA_LOGIN_REEXEC") == "1":
        print(f"❌ venv python ({venv_py}) тоже не видит telethon.")
        print("   Установи зависимости в venv:")
        print(f"       \"{venv_py}\" -m pip install -r requirements.txt")
        sys.exit(1)

    print(f"🔁 Перезапуск через venv: {venv_py}")
    env = os.environ.copy()
    env["LADA_LOGIN_REEXEC"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        rc = subprocess.call(
            [str(venv_py), "-X", "utf8", str(Path(__file__).resolve())],
            cwd=str(PROJECT_ROOT),
            env=env,
        )
    except KeyboardInterrupt:
        sys.exit(1)
    sys.exit(rc)


# ─────────────────────────────── QR-рендер ───────────────────────────────

def _display_qr(url: str):
    try:
        import qrcode
    except ImportError:
        print()
        print("⚠️ Библиотека qrcode не установлена — показываю URL.")
        print("   Установи: pip install qrcode")
        print()
        print("   Открой этот URL в браузере на телефоне, где залогинен Telegram:")
        print(f"       {url}")
        print()
        return

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)

    print()
    print("=" * 70)
    print("  QR-КОД ДЛЯ ВХОДА В TELEGRAM")
    print("=" * 70)
    print("  1. Открой Telegram на телефоне")
    print("  2. Настройки → Устройства → Подключить устройство")
    print("  3. Наведи камеру на QR ниже")
    print()
    qr.print_ascii(invert=True)
    print()
    print("  Если QR не читается камерой — открой этот URL на телефоне:")
    print(f"      {url}")
    print("=" * 70)
    print()


# ─────────────────────────────── подключение ───────────────────────────────

def _tcp_alive(host: str, port: int, timeout: float = 3.0) -> bool:
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


async def _connect_with_failover(TelegramClient, connection, API_ID, API_HASH,
                                 TELEGRAM_PROXIES, TELEGRAM_ALLOW_DIRECT,
                                 TELEGRAM_PROXY_CHECK_TIMEOUT):
    variants = []
    for i, proxy in enumerate(TELEGRAM_PROXIES):
        host, port, secret = proxy
        if not _tcp_alive(host, port, TELEGRAM_PROXY_CHECK_TIMEOUT):
            print(f"⚠️  Прокси #{i} недоступен по TCP ({host}:{port})")
            continue
        print(f"🔎 Прокси #{i} жив, пробую MTProto ({host}:{port})")
        variants.append((i, proxy))

    if TELEGRAM_ALLOW_DIRECT:
        print("⚠️  Пробую прямое подключение")
        variants.append((-1, None))

    last_err = None
    for idx, proxy in variants:
        if proxy is None:
            client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
        else:
            client = TelegramClient(
                SESSION_NAME, API_ID, API_HASH,
                connection=connection.ConnectionTcpMTProxyAbridged,
                proxy=proxy,
            )
        try:
            print(f"⏳ Подключение (вариант #{idx})...")
            await asyncio.wait_for(client.connect(), timeout=15)
            if await client.is_user_authorized():
                print("✅ Сессия уже авторизована.")
                return client, True
            print(f"🔐 Сессия не авторизована (вариант #{idx}) — перехожу к QR.")
            return client, False
        except Exception as e:
            last_err = e
            print(f"❌ Вариант #{idx} упал: {type(e).__name__}: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
            continue

    raise RuntimeError(
        f"Не удалось подключиться ни одним способом. Последняя ошибка: {last_err}"
    )


# ─────────────────────────────── QR-логин ───────────────────────────────

async def _qr_login_flow(client, SessionPasswordNeededError):
    """
    Возвращает True, если вход выполнен.
    """
    attempts = 0
    while True:
        attempts += 1
        try:
            qr_login = await client.qr_login()
        except Exception as e:
            print(f"❌ Не удалось создать QR-логин: {type(e).__name__}: {e}")
            return False

        _display_qr(qr_login.url)

        try:
            print("⏳ Жду сканирования...")
            await qr_login.wait()
            print("✅ QR отсканирован.")
            return True
        except asyncio.TimeoutError:
            print(f"⏰ QR истёк (попытка {attempts}). Генерирую новый...\n")
            continue
        except SessionPasswordNeededError:
            print()
            print("🔐 На аккаунте включена двухфакторная аутентификация (2FA).")
            password = getpass.getpass("   Введи пароль 2FA: ").strip()
            if not password:
                print("❌ Пустой пароль. Выход.")
                return False
            try:
                await client.sign_in(password=password)
                print("✅ 2FA пройдена.")
                return True
            except Exception as e:
                print(f"❌ Ошибка 2FA: {type(e).__name__}: {e}")
                return False
        except Exception as e:
            print(f"❌ Ошибка ожидания QR: {type(e).__name__}: {e}")
            return False


# ─────────────────────────────── main ───────────────────────────────

async def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    from telethon import TelegramClient, connection
    from telethon.errors import SessionPasswordNeededError
    from config import (
        API_ID, API_HASH,
        TELEGRAM_PROXIES, TELEGRAM_ALLOW_DIRECT, TELEGRAM_PROXY_CHECK_TIMEOUT,
    )

    print("=" * 70)
    print("🔐 Вход в Telegram через QR-код")
    print("=" * 70)
    print()

    client = None
    try:
        client, already_authed = await _connect_with_failover(
            TelegramClient, connection, API_ID, API_HASH,
            TELEGRAM_PROXIES, TELEGRAM_ALLOW_DIRECT, TELEGRAM_PROXY_CHECK_TIMEOUT,
        )

        if already_authed:
            me = await client.get_me()
            print()
            print("=" * 70)
            print("✅ Сессия уже активна")
            print(f"   Имя:       {me.first_name}")
            print(f"   Username:  @{me.username if me.username else '—'}")
            print(f"   ID:        {me.id}")
            print("=" * 70)
            return

        success = await _qr_login_flow(client, SessionPasswordNeededError)
        if not success:
            print()
            print("❌ Вход не выполнен.")
            sys.exit(1)

        me = await client.get_me()
        print()
        print("=" * 70)
        print("✅ Вход выполнен успешно")
        print(f"   Имя:       {me.first_name}")
        print(f"   Username:  @{me.username if me.username else '—'}")
        print(f"   ID:        {me.id}")
        print("=" * 70)
        print()
        print(f"Файл сессии: {SESSION_NAME}.session")
        print("Теперь можно запускать run.bat")
        print()

    except Exception as e:
        print()
        print(f"❌ Ошибка: {type(e).__name__}: {e}")
        print()
        print("Что делать:")
        print("  • Если ошибка про повреждённую сессию — удали файл")
        print(f"        {SESSION_NAME}.session")
        print("    и запусти login.py снова.")
        print("  • Если не подключается — проверь интернет/прокси.")
        sys.exit(1)
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    _reexec_in_venv_if_needed()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n❌ Прервано пользователем")
        sys.exit(1)