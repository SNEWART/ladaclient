# run.py — запускает main.py и перезапускает его при изменении любого .py

import subprocess
import sys
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


IGNORED_FILES = {"run.py"}
IGNORED_DIRS = {"venv", ".venv", "data", "__pycache__", ".git", "persona"}


class Reloader(FileSystemEventHandler):
    def __init__(self):
        self.process = None
        self.start_process()

    def start_process(self):
        if self.process:
            print("🛑 Останавливаю main.py...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        print("▶️  Запускаю main.py...\n")
        self.process = subprocess.Popen([sys.executable, "main.py"])

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
        print(f"\n🔄 Изменён {path.name}")
        self.start_process()


if __name__ == "__main__":
    handler = Reloader()
    observer = Observer()
    observer.schedule(handler, ".", recursive=True)
    observer.start()
    print("👀 Слежу за изменениями .py файлов (Ctrl+C для выхода)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Выход...")
        observer.stop()
        if handler.process:
            handler.process.terminate()
    observer.join()