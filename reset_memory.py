# reset_memory.py — полный сброс памяти Лады.
#
# Что делает:
#   1. Делает бэкап ВСЕГО data/ в _backup_manual/ (в корне проекта)
#   2. Удаляет FAISS индекс и metadata
#   3. Удаляет всю историю диалогов (data/users/*.json)
#   4. Обнуляет: mood, sins, trust, relationships, proposals, first_seen
#   5. Удаляет: рабочую память, unread, cooldown, photo_history,
#      last_activity, last_initiative, last_reflection_focus
#   6. Удаляет логи: monologue, initiative, sleep, emotional_diary
#   7. Оставляет: users_names.json, session
#
# Запуск:
#   python reset_memory.py           # dry-run, показать что будет удалено
#   python reset_memory.py --apply   # применить
#   python reset_memory.py --apply --no-backup  # без бэкапа (не советую)

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

DATA = Path("data")
# FIX: бэкап кладём ВНЕ data/, чтобы не копировать папку в саму себя
BACKUP_ROOT = Path("_backup_manual")


# ─── Что удаляем ЦЕЛИКОМ ───
DELETE_FILES = [
    "faiss_index.bin",
    "faiss_metadata.json",
    "faiss_index.partial",
    "faiss_rebuild_progress.json",
    "working_memory.md",
    "unread.json",
    "cooldown.json",
    "photo_history.json",
    "last_activity.json",
    "last_initiative.json",
    "last_reflection_focus.txt",
    "mode.txt",
    "emotional_diary.log",
    "monologue.log",
    "initiative.log",
    "sleep.log",
]

# ─── Что ОБНУЛЯЕМ (перезаписываем дефолтным значением) ───
RESET_FILES = {
    "mood.json": {"mood": "нейтральная"},
    "sins.json": {k: 50.0 for k in [
        "гордыня", "смирение", "жадность", "щедрость",
        "похоть", "целомудрие", "зависть", "доброжелательность",
        "чревоугодие", "умеренность", "гнев", "терпение",
        "уныние", "усердие",
    ]},
    "user_trust.json": {},
    "relationships.json": {},
    "proposals.json": {},
    "first_seen.json": {},
    "user_city.json": {},
}

# ─── Что ОСТАВЛЯЕМ ───
KEEP_FILES = {
    "users_names.json",
}


def _human_kb(path: Path) -> str:
    try:
        kb = path.stat().st_size / 1024
        return f"{kb:.1f} КБ"
    except Exception:
        return "?"


def _ignore_backups(dir_path, names):
    """Исключаем из копирования сами бэкапы, чтобы не тащить мусор."""
    ignored = []
    for n in names:
        full = Path(dir_path) / n
        # не копируем data/backups целиком (там старые бэкапы)
        if full.name == "backups":
            ignored.append(n)
    return ignored


def collect_actions():
    to_delete = []
    to_reset = []
    users_count = 0

    for name in DELETE_FILES:
        p = DATA / name
        if p.exists():
            to_delete.append(p)

    for name in RESET_FILES:
        p = DATA / name
        if p.exists():
            to_reset.append(p)

    users_dir = DATA / "users"
    if users_dir.exists():
        users_count = len(list(users_dir.glob("*.json")))

    return to_delete, to_reset, users_count


def main():
    apply = "--apply" in sys.argv
    no_backup = "--no-backup" in sys.argv

    print("=" * 60)
    print("ПОЛНЫЙ СБРОС ПАМЯТИ ЛАДЫ")
    print("=" * 60)

    if not DATA.exists():
        print(f"❌ Папка {DATA} не найдена. Запусти из корня проекта.")
        sys.exit(1)

    to_delete, to_reset, users_count = collect_actions()

    print()
    print(f"📁 DATA: {DATA.resolve()}")
    print()

    if to_delete:
        print(f"🗑  УДАЛИТЬ файлы ({len(to_delete)}):")
        for p in to_delete:
            print(f"   - {p.name:40s} {_human_kb(p)}")
    else:
        print("🗑  Нечего удалять в корне data/")

    if to_reset:
        print()
        print(f"♻️  ОБНУЛИТЬ файлы ({len(to_reset)}):")
        for p in to_reset:
            print(f"   - {p.name}")

    print()
    if users_count:
        print(f"💬 УДАЛИТЬ историю диалогов: {users_count} файлов в data/users/")
    else:
        print("💬 История диалогов пуста")

    print()
    print("✅ ОСТАВЛЯЕМ:")
    for name in KEEP_FILES:
        p = DATA / name
        marker = "✓" if p.exists() else "·"
        print(f"   {marker} {name}")
    print("   ✓ config.py (не трогаем)")
    print("   ✓ persona/ (не трогаем)")
    print("   ✓ session-файл telegram (не трогаем)")

    total_actions = len(to_delete) + len(to_reset) + (1 if users_count else 0)
    print()
    print(f"ИТОГО действий: {total_actions}")

    if not apply:
        print()
        print("Это DRY-RUN. Ничего не изменено.")
        print("Чтобы применить: python reset_memory.py --apply")
        return

    print()
    print("⚠️  ВНИМАНИЕ: все эти данные будут УДАЛЕНЫ безвозвратно")
    print("    (кроме бэкапа, если он делается).")
    ans = input("Продолжить? Введи 'да' для подтверждения: ").strip().lower()
    if ans != "да":
        print("Отменено.")
        return

    # ─── БЭКАП ───
    if not no_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = BACKUP_ROOT / f"data_{ts}"
        try:
            backup_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                DATA, backup_dir,
                ignore=_ignore_backups,
                dirs_exist_ok=False,
            )
            print(f"💾 Бэкап data/ → {backup_dir}")
        except Exception as e:
            print(f"❌ Бэкап не удался: {e}")
            print("   Прерываю, чтобы не потерять данные.")
            sys.exit(1)
    else:
        print("⚠️  Бэкап отключён (--no-backup), удаляю как есть")

    # ─── УДАЛЕНИЕ ───
    print()
    print("Начинаю чистку...")

    for p in to_delete:
        try:
            p.unlink()
            print(f"   🗑  {p.name}")
        except Exception as e:
            print(f"   ⚠️  {p.name}: {e}")

    for p in to_reset:
        name = p.name
        try:
            default = RESET_FILES[name]
            p.write_text(
                json.dumps(default, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"   ♻️  {name} → сброшено")
        except Exception as e:
            print(f"   ⚠️  {name}: {e}")

    users_dir = DATA / "users"
    if users_dir.exists():
        removed = 0
        for f in users_dir.glob("*.json"):
            try:
                f.unlink()
                removed += 1
            except Exception as e:
                print(f"   ⚠️  {f.name}: {e}")
        print(f"   💬 Удалено историй диалогов: {removed}")

    print()
    print("=" * 60)
    print("✅ СБРОС ЗАВЕРШЁН")
    print("=" * 60)
    print()
    if not no_backup:
        print(f"Бэкап сохранён: {backup_dir}")
        print("Если что — можно восстановить копированием.")
    print()
    print("Теперь запусти бота:")
    print("   python run.py")
    print()
    print("Лада начнёт с нуля: чистый FAISS, чистые шкалы, чистые отношения.")


if __name__ == "__main__":
    main()