# clean_memory.py — Полная очистка памяти Лады
#
# ⚠️ ВНИМАНИЕ: Это удалит ВСЮ память, историю диалогов, эмоции, отношения!
# Используйте только если хотите начать с чистого листа.

import shutil
from pathlib import Path

DATA_DIR = Path("data")

def clean_all_memory():
    """Удалить всю память Лады."""

    print("=" * 60)
    print("🧹 ОЧИСТКА ПАМЯТИ ЛАДЫ")
    print("=" * 60)
    print()

    if not DATA_DIR.exists():
        print("✅ Папка data/ не существует — память уже чиста.")
        return

    # Список файлов/папок для удаления
    items_to_clean = [
        DATA_DIR / "lada.db",           # SQLite база
        DATA_DIR / "lada.db-wal",       # WAL файл SQLite
        DATA_DIR / "lada.db-shm",       # SHM файл SQLite
        DATA_DIR / "lancedb",           # Векторная память
        DATA_DIR / "faiss",             # Старая FAISS память (если есть)
        DATA_DIR / "diary",             # Дневник (если есть)
        DATA_DIR / "sleep_backups",     # Бэкапы после сна
    ]

    cleaned = []
    not_found = []

    for item in items_to_clean:
        if item.exists():
            try:
                if item.is_file():
                    item.unlink()
                    cleaned.append(f"📄 {item.name}")
                elif item.is_dir():
                    shutil.rmtree(item)
                    cleaned.append(f"📁 {item.name}/")
            except Exception as e:
                print(f"⚠️ Не удалось удалить {item.name}: {e}")
        else:
            not_found.append(item.name)

    print("Удалено:")
    if cleaned:
        for item in cleaned:
            print(f"  ✅ {item}")
    else:
        print("  (ничего не найдено)")

    print()
    print("Не найдено (это нормально):")
    if not_found:
        for item in not_found[:5]:  # Показываем только первые 5
            print(f"  ⊘ {item}")
        if len(not_found) > 5:
            print(f"  ... и ещё {len(not_found) - 5}")

    print()
    print("=" * 60)
    print("✅ ОЧИСТКА ЗАВЕРШЕНА")
    print("=" * 60)
    print()
    print("Память Лады полностью очищена.")
    print("При следующем запуске бот начнёт с чистого листа:")
    print("  - Никакой истории диалогов")
    print("  - Никаких воспоминаний")
    print("  - Нулевые эмоции и отношения")
    print("  - Чистая векторная память")
    print()
    print("🚀 Готово к работе с новой оптимизированной архитектурой!")
    print()


if __name__ == "__main__":
    import sys

    # Проверка безопасности
    print()
    print("⚠️  ВНИМАНИЕ: Эта операция удалит ВСЮ память Лады!")
    print()
    print("Это включает:")
    print("  - Всю историю диалогов")
    print("  - Все воспоминания (векторная память)")
    print("  - Все эмоции и отношения")
    print("  - Все настройки пользователей")
    print("  - Все обещания, темы, состояния")
    print()

    answer = input("Вы уверены? Введите 'ДА' для подтверждения: ")

    if answer.strip().upper() == "ДА":
        print()
        clean_all_memory()
    else:
        print()
        print("❌ Отменено. Память не изменена.")
        print()
        sys.exit(0)
