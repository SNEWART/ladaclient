# clean_faiss.py — одноразовая чистка мусора в FAISS.
# Удаляет записи с маркерами, звёздочками и утечками промптов.
#
# Запуск:
#   python clean_faiss.py           # dry-run, только показать
#   python clean_faiss.py --apply   # применить

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

META = Path("data") / "faiss_metadata.json"
BACKUP = Path("data") / "backups"
BACKUP.mkdir(parents=True, exist_ok=True)


BAD_PATTERNS = [
    r'\[STICKER[^\]]*\]',
    r'\[PROPOSE:\s*(?:lover|husband)\s*\]',
    r'\[IMAGE\]\s*(?:hint|pet):',
    r'\[SILENCE\]',
    r'\[SEARCH\]',
    r'\*[^*\n]{2,80}\*',                # *действия*
    r'^Вот что сейчас в твоей голове',  # утечка промпта монолога
    r'^Текущая рабочая память',         # утечка промпта WM (на будущее)
    r'^Вот что ты помнишь',             # утечка FAISS-блока
    r'^\s*\{.*"emotion"',               # утечка JSON-промпта
]


def is_garbage(text: str) -> str | None:
    """Возвращает название причины, если запись мусорная."""
    if not text or len(text.strip()) < 5:
        return "слишком короткое"
    for pat in BAD_PATTERNS:
        if re.search(pat, text):
            return f"паттерн {pat[:30]}"
    return None


def main():
    apply = "--apply" in sys.argv

    if not META.exists():
        print(f"❌ {META} не найден")
        return

    data = json.loads(META.read_text(encoding="utf-8"))
    print(f"Всего записей: {len(data)}")

    clean, garbage = [], []
    for item in data:
        reason = is_garbage(item.get("text", ""))
        if reason:
            garbage.append((item, reason))
        else:
            clean.append(item)

    print(f"Чистых: {len(clean)}")
    print(f"Мусорных: {len(garbage)}")

    if garbage:
        print("\n--- Что удаляем (первые 20) ---")
        for item, reason in garbage[:20]:
            t = item.get("text", "")[:80]
            cat = item.get("category", "?")
            print(f"  [{cat}] {reason[:25]:25s} | {t!r}")

    if not apply:
        print("\nЭто dry-run. Запусти с --apply, чтобы удалить.")
        return

    if not garbage:
        print("\nНечего удалять.")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP / f"faiss_metadata_PRECLEAN_{ts}.json"
    shutil.copy2(META, backup_path)
    print(f"\n💾 Бэкап: {backup_path}")

    META.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ Удалено {len(garbage)} записей. Осталось {len(clean)}.")
    print(f"\n⚠️  Теперь пересобери индекс:")
    print(f"    python rebuild_faiss.py")


if __name__ == "__main__":
    main()