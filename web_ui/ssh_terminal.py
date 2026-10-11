# web_ui/ssh_terminal.py — WebSocket → asyncssh → интерактивный shell.
# Фронт: xterm.js. Бэк: FastAPI WebSocket + /hosts. Транспорт: asyncssh.

import asyncio
import json
from pathlib import Path

import asyncssh
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from web_ui.auth import _validate_session, _auth_enabled, SESSION_COOKIE, require_auth

router = APIRouter(prefix="/api/ssh", tags=["ssh"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOSTS_FILE = PROJECT_ROOT / "web_ui" / "ssh_hosts.json"

DEFAULT_COLS = 120
DEFAULT_ROWS = 30
READ_CHUNK = 8192


def _load_hosts() -> list:
    if not HOSTS_FILE.exists():
        return []
    try:
        data = json.loads(HOSTS_FILE.read_text(encoding="utf-8"))
        return data.get("hosts", []) or []
    except Exception as e:
        print(f"⚠️ ssh_hosts.json: {e}")
        return []


def _find_host(host_id: str) -> dict | None:
    for h in _load_hosts():
        if h.get("id") == host_id:
            return h
    return None


def _public_host(h: dict) -> dict:
    return {
        "id": h.get("id"),
        "name": h.get("name", h.get("id")),
        "host": h.get("host"),
        "port": h.get("port", 22),
        "user": h.get("user"),
        "commands": [
            {"index": i, "name": c.get("name", f"cmd {i}"), "cmd": c.get("cmd", "")}
            for i, c in enumerate(h.get("commands", []) or [])
        ],
    }


# ─────────────── HTTP: список хостов ───────────────

@router.get("/hosts")
async def list_hosts(_: bool = Depends(require_auth)):
    return [_public_host(h) for h in _load_hosts()]


# ─────────────── WebSocket: терминал ───────────────

@router.websocket("/terminal")
async def ssh_terminal(websocket: WebSocket):
    host_id = websocket.query_params.get("host_id")
    session_token = websocket.cookies.get(SESSION_COOKIE)

    if _auth_enabled():
        if not session_token or not _validate_session(session_token):
            await websocket.close(code=4401)
            return

    host = _find_host(host_id) if host_id else None
    if not host:
        await websocket.accept()
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Хост '{host_id}' не найден в ssh_hosts.json",
        }))
        await websocket.close()
        return

    await websocket.accept()

    connect_kwargs = {
        "host": host["host"],
        "port": int(host.get("port", 22)),
        "username": host["user"],
        "known_hosts": None,
    }
    key_path = (host.get("key_path") or "").strip()
    password = (host.get("password") or "").strip()
    if key_path:
        connect_kwargs["client_keys"] = [key_path]
    if password:
        connect_kwargs["password"] = password

    try:
        conn = await asyncssh.connect(**connect_kwargs)
    except Exception as e:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"SSH: {type(e).__name__}: {e}",
        }))
        await websocket.close()
        return

    try:
        proc = await conn.create_process(
            term_type="xterm-256color",
            term_size=(DEFAULT_COLS, DEFAULT_ROWS),
            encoding=None,
        )
    except Exception as e:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"shell: {e}",
        }))
        await conn.close()
        await websocket.close()
        return

    await websocket.send_text(json.dumps({
        "type": "connected",
        "host": host.get("name", host_id),
    }))

    stop = asyncio.Event()

    async def ws_to_ssh():
        try:
            while not stop.is_set():
                msg = await websocket.receive_text()
                try:
                    data = json.loads(msg)
                except Exception:
                    continue
                t = data.get("type")
                if t == "input":
                    payload = data.get("data", "")
                    if payload:
                        proc.stdin.write(payload.encode("utf-8"))
                elif t == "resize":
                    cols = int(data.get("cols", DEFAULT_COLS))
                    rows = int(data.get("rows", DEFAULT_ROWS))
                    try:
                        proc.change_terminal_size(cols, rows)
                    except Exception:
                        pass
        except WebSocketDisconnect:
            stop.set()
        except Exception:
            stop.set()

    async def ssh_to_ws():
        try:
            while not stop.is_set():
                chunk = await proc.stdout.read(READ_CHUNK)
                if not chunk:
                    break
                if isinstance(chunk, bytes):
                    text = chunk.decode("utf-8", errors="replace")
                else:
                    text = chunk
                await websocket.send_text(json.dumps({
                    "type": "output",
                    "data": text,
                }))
        except Exception:
            pass
        finally:
            stop.set()

    async def ssh_stderr_to_ws():
        try:
            while not stop.is_set():
                chunk = await proc.stderr.read(READ_CHUNK)
                if not chunk:
                    break
                if isinstance(chunk, bytes):
                    text = chunk.decode("utf-8", errors="replace")
                else:
                    text = chunk
                await websocket.send_text(json.dumps({
                    "type": "output",
                    "data": text,
                }))
        except Exception:
            pass

    try:
        await asyncio.gather(ws_to_ssh(), ssh_to_ws(), ssh_stderr_to_ws())
    finally:
        stop.set()
        try:
            proc.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass