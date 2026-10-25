# web_ui/commands_api.py — ручной запуск предопределённых команд.
#
# Белый список действий. Никаких свободных команд из веба —
# только то, что разрешено в ACTIONS. Результат возвращается
# целиком в JSON.

import asyncio
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web_ui.auth import require_auth

router = APIRouter(prefix="/api/commands", tags=["commands"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():
    VENV_PY = Path(sys.executable)

COMMAND_TIMEOUT = 600
MAX_OUTPUT = 80_000


def _python() -> str:
    return str(VENV_PY)


# ─── Белый список ───

ACTIONS = {
    "pytest": {
        "name": "🧪 Тесты",
        "desc": "pytest -q в папке tests/",
        "cmd": [_python(), "-m", "pytest", "-q"],
        "cwd": PROJECT_ROOT,
    },
    "git_pull": {
        "name": "📥 Git pull",
        "desc": "git pull в корне проекта",
        "cmd": ["git", "pull"],
        "cwd": PROJECT_ROOT,
    },
    "git_status": {
        "name": "📋 Git status",
        "desc": "git status --short",
        "cmd": ["git", "status", "--short"],
        "cwd": PROJECT_ROOT,
    },
    "rebuild_memory": {
        "name": "🧹 Пересобрать индекс памяти",
        "desc": "проверка LanceDB и пересчёт метаданных",
        "cmd": [_python(), "-c",
                "from lada.core.lancedb_memory import lance_mem; "
                "print('записей:', lance_mem.count()); "
                "print('users:', len(lance_mem.list_users()))"],
        "cwd": PROJECT_ROOT,
    },
    "memory_health": {
        "name": "🩺 Проверка эмбеддингов",
        "desc": "health_check LanceDB",
        "cmd": [_python(), "-c",
                "from lada.core.lancedb_memory import lance_mem; "
                "ok, msg = lance_mem.health_check(); "
                "print('OK' if ok else 'FAIL', msg)"],
        "cwd": PROJECT_ROOT,
    },
    "memory_stats": {
        "name": "📊 Статистика памяти",
        "desc": "разбивка по категориям и пользователям",
        "cmd": [_python(), "-c",
                "from lada.core.lancedb_memory import lance_mem; "
                "from collections import Counter; "
                "items = lance_mem.metadata; "
                "print('Всего:', len(items)); "
                "print('Категории:', dict(Counter(m.get('category','?') for m in items))); "
                "print('С мета:', sum(1 for m in items if m.get('valence') is not None))"],
        "cwd": PROJECT_ROOT,
    },
    "backup_data": {
        "name": "💾 Бэкап data/",
        "desc": "копия data/ в _backup_manual/manual_<ts>/",
        "custom": "backup",
    },
    "restart_main": {
        "name": "🔄 Перезапустить main.py",
        "desc": "touch main.py — watchdog перезапустит процесс",
        "custom": "restart_main",
    },
}


class RunRequest(BaseModel):
    action: str


def _do_backup() -> str:
    src = PROJECT_ROOT / "data"
    if not src.exists():
        return "⚠️ data/ не найдена"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = PROJECT_ROOT / "_backup_manual" / f"manual_{ts}"
    dst.parent.mkdir(parents=True, exist_ok=True)

    def _ignore(dir_path, names):
        return [n for n in names if n == "backups"]

    try:
        shutil.copytree(src, dst, ignore=_ignore)
    except Exception as e:
        return f"❌ Бэкап упал: {e}"

    size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
    mb = size / 1024 / 1024
    return f"✅ Бэкап: {dst}\n   {mb:.1f} МБ"


def _do_restart_main() -> str:
    main_py = PROJECT_ROOT / "main.py"
    if not main_py.exists():
        return "❌ main.py не найден"
    try:
        os.utime(main_py, None)
    except Exception as e:
        return f"❌ Не удалось обновить mtime: {e}"
    return "✅ main.py тронут. Watchdog перезапустит через 1–2 секунды."


@router.get("/list")
async def list_actions(_: bool = Depends(require_auth)):
    out = []
    for key, spec in ACTIONS.items():
        out.append({
            "id": key,
            "name": spec.get("name", key),
            "desc": spec.get("desc", ""),
        })
    return out


@router.post("/run")
async def run_action(req: RunRequest, _: bool = Depends(require_auth)):
    spec = ACTIONS.get(req.action)
    if not spec:
        raise HTTPException(404, f"Действие '{req.action}' не найдено")

    started = datetime.now()

    # Кастомные действия без subprocess
    if "custom" in spec:
        try:
            if spec["custom"] == "backup":
                output = await asyncio.to_thread(_do_backup)
                ok = not output.startswith("❌") and not output.startswith("⚠️")
            elif spec["custom"] == "restart_main":
                output = await asyncio.to_thread(_do_restart_main)
                ok = not output.startswith("❌")
            else:
                output = f"❌ Неизвестное кастомное действие: {spec['custom']}"
                ok = False
        except Exception as e:
            output = f"❌ Ошибка: {e}"
            ok = False

        duration = (datetime.now() - started).total_seconds()
        return {
            "action": req.action,
            "name": spec.get("name"),
            "ok": ok,
            "exit_code": 0 if ok else -1,
            "output": output[:MAX_OUTPUT],
            "duration": round(duration, 2),
        }

    # Обычные команды
    cmd = spec.get("cmd")
    cwd = spec.get("cwd", PROJECT_ROOT)

    if not cmd:
        raise HTTPException(500, "Пустая команда")

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=creationflags,
        )
    except FileNotFoundError as e:
        return {
            "action": req.action,
            "name": spec.get("name"),
            "ok": False,
            "exit_code": -1,
            "output": f"❌ Команда не найдена: {e}",
            "duration": 0,
        }

    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=COMMAND_TIMEOUT
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "action": req.action,
            "name": spec.get("name"),
            "ok": False,
            "exit_code": -1,
            "output": f"⏰ Таймаут {COMMAND_TIMEOUT}с",
            "duration": COMMAND_TIMEOUT,
        }

    try:
        output = stdout.decode("utf-8", errors="replace")
    except Exception:
        output = str(stdout)

    duration = (datetime.now() - started).total_seconds()
    return {
        "action": req.action,
        "name": spec.get("name"),
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": output[:MAX_OUTPUT],
        "duration": round(duration, 2),
    }