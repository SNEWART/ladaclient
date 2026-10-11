# run.py — супервизор: следит за файлами и перезапускает main.py при падении

import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


PROJECT_ROOT = Path(__file__).parent.resolve()
LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_RETENTION_DAYS = 14

IGNORED_FILES = {"run.py"}
IGNORED_DIRS = {
    "venv", ".venv", "data", "__pycache__", ".git",
    "persona", "tests", ".pytest_cache",
    ".idea", ".vscode", ".mypy_cache", ".ruff_cache",
    "backups", "_backup_manual",
}

RESTART_DELAYS = [2, 4, 8, 16, 30]
STABLE_THRESHOLD = 60


def _today_log() -> Path:
    return LOG_DIR / f"main_{datetime.now().strftime('%Y%m%d')}.log"


def _clean_old_logs():
    try:
        cutoff = time.time() - LOG_RETENTION_DAYS * 86400
        for f in LOG_DIR.glob("main_*.log"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
    except Exception as e:
        print(f"⚠️ Не удалось почистить логи: {e}")


def _log_line(text: str):
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
            sys.stdout.flush()
        except Exception:
            pass
    try:
        with open(_today_log(), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except Exception:
        pass


def _child_env() -> dict:
    """Окружение для main.py: UTF-8 + без буферизации."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"  # важно: main.py печатает сразу
    env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
    return env


class Reloader(FileSystemEventHandler):
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.started_at: float = 0
        self.fail_count = 0
        self._stop = False
        self._reader_thread: threading.Thread | None = None
        self.start_process()

    def start_process(self):
        if self.process and self.process.poll() is None:
            _log_line("🛑 Останавливаю main.py...")
            self._terminate_process()

        _log_line("▶️  Запускаю main.py...")
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        self.process = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-u", "main.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_child_env(),
            creationflags=creationflags,
        )
        self.started_at = time.time()
        self._start_reader(self.process)

    def _start_reader(self, process: subprocess.Popen):
        def reader():
            try:
                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    _log_line(line.rstrip())
            except Exception:
                pass
            try:
                process.stdout.close()
            except Exception:
                pass

        self._reader_thread = threading.Thread(target=reader, daemon=True)
        self._reader_thread.start()

    def _terminate_process(self):
        if not self.process:
            return
        try:
            if os.name == "nt":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name in IGNORED_FILES:
            return
        if any(part in IGNORED_DIRS for part in path.parts):
            return
        if not path.name.endswith(".py"):
            return
        _log_line(f"🔄 Изменён {path.name}")
        self.fail_count = 0
        self.start_process()

    def check_and_restart(self):
        if self._stop or not self.process:
            return
        retcode = self.process.poll()
        if retcode is None:
            return

        work_time = time.time() - self.started_at
        _log_line(f"💥 main.py упал (код {retcode}), проработал {work_time:.0f}с")

        if work_time > STABLE_THRESHOLD:
            self.fail_count = 0

        delay = RESTART_DELAYS[min(self.fail_count, len(RESTART_DELAYS) - 1)]
        self.fail_count += 1
        _log_line(f"⏳ Перезапуск через {delay}с...")

        time.sleep(delay)
        if not self._stop:
            self.start_process()

    def stop(self):
        self._stop = True
        self._terminate_process()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    _clean_old_logs()
    handler = Reloader()
    observer = Observer()
    observer.schedule(handler, str(PROJECT_ROOT), recursive=True)
    observer.start()
    _log_line("👀 Слежу за main.py (Ctrl+C для выхода)")
    _log_line(f"📁 Логи: {LOG_DIR}")

    try:
        while True:
            time.sleep(1)
            handler.check_and_restart()
    except KeyboardInterrupt:
        _log_line("🛑 Выход...")
    finally:
        observer.stop()
        handler.stop()
    observer.join()