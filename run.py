# run.py — единый супервизор: .env → ollama serve → Stable Diffusion → main.py
# + watchdog: правки .py перезапускают только main.py.
#
# FIX: FileWatcher теперь проверяет mtime перед перезапуском.
# На Windows чтение файлов часто приходит как "modified" (антивирус,
# OneDrive, NTFS) — раньше это вызывало ложные перезапуски и крашило
# веб-панель при открытии вкладок.

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _WATCHDOG_OK = True
except Exception as e:
    print(f"⚠️ watchdog недоступен ({e}), хот-релоад отключён")
    Observer = None

    class FileSystemEventHandler:
        pass

    _WATCHDOG_OK = False


PROJECT_ROOT = Path(__file__).parent.resolve()
LOG_DIR = PROJECT_ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
PID_FILE = PROJECT_ROOT / "data" / "run.pid"
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
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/tags"


# ------------------------------- .env ------------------------------------

def load_dotenv(path: Path = PROJECT_ROOT / ".env"):
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# ----------------------------- логирование -------------------------------

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


_log_lock = threading.Lock()


def log(text: str):
    with _log_lock:
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


def _child_env(extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
    if extra:
        env.update(extra)
    return env


def _quick_health(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


def _wrap_cmd_for_windows(cmd: list) -> list:
    if os.name != "nt" or not cmd:
        return cmd
    first = cmd[0].lower()
    if first.endswith((".bat", ".cmd")):
        return ["cmd", "/c"] + cmd
    return cmd


def _find_git(sd_cwd: Path | None = None) -> str | None:
    candidates = []
    if sd_cwd:
        candidates += [
            sd_cwd / "git" / "cmd" / "git.exe",
            sd_cwd / "git" / "bin" / "git.exe",
            sd_cwd / "PortableGit" / "cmd" / "git.exe",
            sd_cwd / "PortableGit" / "bin" / "git.exe",
        ]
    for p in (
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\bin\git.exe",
    ):
        candidates.append(Path(p))
    which = shutil.which("git")
    if which:
        candidates.append(Path(which))
    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return str(c)
        except Exception:
            continue
    return None


# ---------------------------- базовый сервис -----------------------------

class Service:
    def __init__(
        self,
        name: str,
        command: list,
        cwd: Path = PROJECT_ROOT,
        env: dict | None = None,
        health_url: str | None = None,
        startup_grace: float = 3.0,
        health_timeout: float = 120.0,
        optional: bool = False,
    ):
        self.name = name
        self.command = command
        self.cwd = cwd
        self.env = env
        self.health_url = health_url
        self.startup_grace = startup_grace
        self.health_timeout = health_timeout
        self.optional = optional

        self.process: subprocess.Popen | None = None
        self.started_at: float = 0
        self.fail_count = 0
        self._stop = False
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._stop:
                return
            if self.process and self.process.poll() is None:
                return

            cmd = _wrap_cmd_for_windows(self.command)
            log(f"[{self.name}] ▶️  {' '.join(cmd)}")

            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=str(self.cwd),
                    bufsize=1,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=self.env or os.environ.copy(),
                    creationflags=creationflags,
                )
            except FileNotFoundError as e:
                if self.optional:
                    log(f"[{self.name}] ⚠️ не найден ({e}) — пропускаю.")
                    self._stop = True
                    return
                raise
            self.started_at = time.time()
            self._start_reader(self.process)

    def restart(self):
        log(f"[{self.name}] 🔄 Перезапуск по запросу")
        self._terminate()
        self.fail_count = 0
        self.start()

    def stop(self, graceful_timeout: float = 8.0):
        self._stop = True
        self._terminate(timeout=graceful_timeout)

    def is_alive(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def wait_healthy(self) -> bool:
        if not self.health_url:
            time.sleep(self.startup_grace)
            return True
        deadline = time.time() + self.health_timeout
        last_report = 0.0
        start = time.time()
        while time.time() < deadline:
            if self._stop:
                return False
            if not self.is_alive():
                return False
            if _quick_health(self.health_url):
                return True
            elapsed = time.time() - start
            if elapsed - last_report >= 10:
                log(f"[{self.name}] ещё жду... ({elapsed:.0f}с)")
                last_report = elapsed
            time.sleep(1.5)
        return False

    def check_and_restart(self):
        if self._stop or not self.process:
            return
        retcode = self.process.poll()
        if retcode is None:
            return

        work_time = time.time() - self.started_at
        log(f"[{self.name}] 💥 упал (код {retcode}), проработал {work_time:.0f}с")

        if work_time > STABLE_THRESHOLD:
            self.fail_count = 0

        delay = RESTART_DELAYS[min(self.fail_count, len(RESTART_DELAYS) - 1)]
        self.fail_count += 1
        log(f"[{self.name}] ⏳ перезапуск через {delay}с...")

        def _delayed():
            time.sleep(delay)
            if not self._stop:
                self.start()

        threading.Thread(target=_delayed, daemon=True).start()

    def _start_reader(self, process: subprocess.Popen):
        def reader():
            try:
                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    log(f"[{self.name}] {line.rstrip()}")
            except Exception:
                pass
            try:
                process.stdout.close()
            except Exception:
                pass

        threading.Thread(target=reader, daemon=True).start()

    def _terminate(self, timeout: float = 5.0):
        proc = self.process
        if not proc or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    proc.terminate()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                log(f"[{self.name}] ⚠️ не завершился мягко, kill")
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ---------------------------- файловый watcher ---------------------------

class FileWatcher(FileSystemEventHandler):
    """
    FIX: следит за .py, но перезапускает только если mtime реально
    изменился. На Windows чтение файла, антивирус, OneDrive и
    индексатор приходят как 'modified' — раньше это крашило панель.
    """

    def __init__(self, service: Service):
        self.service = service
        self._mtimes: dict = {}
        self._init_mtimes()

    def _init_mtimes(self):
        try:
            for f in PROJECT_ROOT.rglob("*.py"):
                if any(part in IGNORED_DIRS for part in f.parts):
                    continue
                try:
                    self._mtimes[str(f)] = f.stat().st_mtime
                except Exception:
                    pass
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

        try:
            current_mtime = path.stat().st_mtime
        except Exception:
            return

        old_mtime = self._mtimes.get(str(path))
        if old_mtime is not None and abs(current_mtime - old_mtime) < 0.01:
            # mtime не изменился — ложное срабатывание, игнорируем
            return

        self._mtimes[str(path)] = current_mtime
        try:
            rel = path.relative_to(PROJECT_ROOT)
        except Exception:
            rel = path
        log(f"🔄 Изменён {rel}")
        self.service.restart()


# ------------------------------- сборка ----------------------------------

def build_services(args):
    deps: list = []

    # --- Ollama ---
    if not args.no_ollama:
        ollama_url = os.getenv("LADA_OLLAMA_URL", DEFAULT_OLLAMA_URL)
        ollama_cmd = os.getenv("LADA_OLLAMA_CMD", "ollama serve")
        if _quick_health(ollama_url):
            log("✅ Ollama уже запущен — не трогаю.")
        else:
            deps.append(Service(
                name="ollama",
                command=shlex.split(ollama_cmd, posix=(os.name != "nt")),
                health_url=ollama_url,
                startup_grace=3,
                health_timeout=60,
                optional=True,
            ))

    # --- Stable Diffusion ---
    if not args.no_sd:
        sd_cmd_str = os.getenv("LADA_SD_CMD", "").strip()
        if not sd_cmd_str:
            log("ℹ️  LADA_SD_CMD не задан — SD пропускаю.")
        else:
            sd_cwd = Path(os.getenv("LADA_SD_CWD", str(PROJECT_ROOT))).resolve()
            sd_url = os.getenv("LADA_SD_URL", "").strip() or None
            sd_timeout = float(os.getenv("LADA_SD_HEALTH_TIMEOUT", "300"))

            parts = shlex.split(sd_cmd_str, posix=False)
            first = parts[0].strip('"')
            if not Path(first).is_absolute():
                candidate = sd_cwd / first
                if candidate.exists():
                    parts[0] = str(candidate)

            sd_env = _child_env()
            git_path = _find_git(sd_cwd)
            if git_path:
                sd_env["GIT"] = git_path
                git_dir = str(Path(git_path).parent)
                sd_env["PATH"] = git_dir + os.pathsep + sd_env.get("PATH", "")
                log(f"[sd] git найден: {git_path}")
            else:
                log("[sd] ⚠️ git не найден")

            deps.append(Service(
                name="sd",
                command=parts,
                cwd=sd_cwd,
                env=sd_env,
                health_url=sd_url,
                startup_grace=5,
                health_timeout=sd_timeout,
                optional=True,
            ))

    # --- main.py ---
    main_service = Service(
        name="main",
        command=[sys.executable, "-X", "utf8", "-u", "main.py"],
        env=_child_env(),
    )

    return deps, main_service


def parse_args():
    p = argparse.ArgumentParser(description="Lada supervisor")
    p.add_argument("--no-ollama", action="store_true")
    p.add_argument("--no-sd", action="store_true")
    p.add_argument("--no-watch", action="store_true")
    p.add_argument("--only", choices=["main", "ollama", "sd"])
    return p.parse_args()


# ------------------------------- main ------------------------------------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    _clean_old_logs()
    load_dotenv()
    args = parse_args()

    if args.only == "main":
        args.no_ollama = True
        args.no_sd = True
    elif args.only == "ollama":
        args.no_sd = True
    elif args.only == "sd":
        args.no_ollama = True

    deps, main_service = build_services(args)
    all_services: list = list(deps)

    run_main = args.only in (None, "main")
    if run_main:
        all_services.append(main_service)

    for s in deps:
        s.start()

    for s in deps:
        ok = s.wait_healthy()
        log(f"[{s.name}] {'✅ готов' if ok else '⚠️ не отвечает, продолжаю'}")

    if run_main:
        main_service.start()

    observer = None
    if not args.no_watch and run_main and _WATCHDOG_OK:
        try:
            observer = Observer()
            observer.schedule(FileWatcher(main_service), str(PROJECT_ROOT), recursive=True)
            observer.start()
            log("👀 Watchdog запущен (mtime-проверка)")
        except Exception as e:
            log(f"⚠️ Watchdog не запустился: {e}")
            observer = None

    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass

    log("👀 Супервизор запущен (Ctrl+C для выхода)")
    log(f"📁 Логи: {LOG_DIR}")
    log(f"🆔 PID: {os.getpid()}")

    try:
        while True:
            time.sleep(1)
            for s in all_services:
                s.check_and_restart()
    except KeyboardInterrupt:
        log("🛑 Выход...")
    finally:
        if observer:
            observer.stop()
        for s in reversed(all_services):
            s.stop()
        if observer:
            observer.join()
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()