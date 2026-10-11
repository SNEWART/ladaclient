# clean_bad_observations.py — удаляет ошибочные наблюдения из LanceDB.
#
# Использует LanceDB delete() — точечное удаление без пересчёта
# эмбеддингов. Быстро и безопасно.
#
# Запуск:
#   python clean_bad_observations.py                  # dry-run
#   python clean_bad_observations.py --apply          # удалить
#   python clean_bad_observations.py --text "фраза"   # найти конкретную фразу
#   python clean_bad_observations.py --apply --text "фраза"

import sys
import shutil
from datetime import datetime
from pathlib import Path

from lada.core.lancedb_memory import lance_mem, TABLE_NAME, LANCEDB_DIR


BAD_MARKERS = [
    "здравоохран",
    "медицин",
    "больниц",
    "врач",
]

BACKUP_DIR = Path("data") / "backups"


def find_bad(items, text_filter=None):
    out = []
    for m in items:
        if m.get("category") != "observation":
            continue
        t = (m.get("text") or "").lower()
        if text_filter:
            if text_filter.lower() in t:
                out.append(m)
            continue
        if any(b in t for b in BAD_MARKERS):
            out.append(m)
    return out


def backup_lancedb():
    """Копирует всю папку lancedb в data/backups/ перед удалением."""
    if not LANCEDB_DIR.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"lancedb_preclean_{ts}"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(LANCEDB_DIR, dst)
        return dst
    except Exception as e:
        print(f"⚠️ Бэкап не удался: {e}")
        return None


def main():
    apply = "--apply" in sys.argv

    text_filter = None
    if "--text" in sys.argv:
        i = sys.argv.index("--text")
        if i + 1 < len(sys.argv):
            text_filter = sys.argv[i + 1]

    if lance_mem.table is None:
        print("❌ LanceDB не загружена")
        return

    all_items = list(lance_mem.metadata)
    print(f"Всего записей в LanceDB: {len(all_items)}")

    bad = find_bad(all_items, text_filter)

    if not bad:
        print("✅ Ничего подозрительного не найдено")
        return

    print(f"\nНайдено {len(bad)} подозрительных записей:")
    for m in bad:
        uid = m.get("user_id")
        text = (m.get("text") or "")[:100]
        print(f"  [user_id={uid}] {text}")

    if not apply:
        print("\nЭто dry-run. Запусти с --apply, чтобы удалить.")
        return

    # ─── Бэкап ───
    backup = backup_lancedb()
    if backup:
        print(f"\n💾 Бэкап LanceDB: {backup}")

    # ─── Точечное удаление через LanceDB delete ───
    # Удаляем по совпадению (text, user_id) — чтобы не задеть чужие похожие.
    # LanceDB delete принимает SQL-подобное условие.
    deleted = 0
    for m in bad:
        text = (m.get("text") or "").strip()
        uid = m.get("user_id")
        if not text:
            continue

        # Экранируем одинарные кавычки в тексте
        safe_text = text.replace("'", "''")

        if uid is None:
            where = f"text = '{safe_text}' AND user_id = -1"
        else:
            where = f"text = '{safe_text}' AND user_id = {int(uid)}"

        try:
            lance_mem.table.delete(where)
            deleted += 1
        except Exception as e:
            print(f"   ⚠️ Не удалось удалить «{text[:60]}»: {e}")

    # Сбрасываем кэш metadata в lance_mem
    lance_mem._invalidate_meta_cache()

    print(f"\n✅ Удалено {deleted} из {len(bad)}")
    print(f"   В LanceDB сейчас: {lance_mem.count()} записей")


if __name__ == "__main__":
    main()