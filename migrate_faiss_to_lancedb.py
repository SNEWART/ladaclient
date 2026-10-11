# migrate_faiss_to_lancedb.py — миграция из FAISS в LanceDB.
#
# Запуск:
#   python migrate_faiss_to_lancedb.py            # dry-run
#   python migrate_faiss_to_lancedb.py --apply    # применить

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import faiss
import lancedb
import pyarrow as pa

DATA_DIR = Path("data")
FAISS_INDEX = DATA_DIR / "faiss_index.bin"
FAISS_META = DATA_DIR / "faiss_metadata.json"
LANCEDB_DIR = DATA_DIR / "lancedb"
BACKUP_DIR = DATA_DIR / "faiss_backup"
TABLE_NAME = "memory"


def migrate():
    print("=" * 60)
    print("МИГРАЦИЯ FAISS → LanceDB")
    print("=" * 60)

    if not FAISS_INDEX.exists() or not FAISS_META.exists():
        print("❌ Файлы FAISS не найдены.")
        print(f"   Ожидается: {FAISS_INDEX}, {FAISS_META}")
        return False

    # 1. Загружаем FAISS
    index = faiss.read_index(str(FAISS_INDEX))
    metadata = json.loads(FAISS_META.read_text(encoding="utf-8"))
    print(f"📦 FAISS: {index.ntotal} векторов, "
          f"{len(metadata)} записей metadata")

    if index.ntotal == 0:
        print("⚠️ FAISS-индекс пуст. Нечего мигрировать.")
        return False

    if index.ntotal != len(metadata):
        print(f"⚠️  Рассинхрон: {index.ntotal} vs {len(metadata)}. "
              f"Возьмём min().")

    n = min(index.ntotal, len(metadata))
    vectors = index.reconstruct_n(0, n)
    dim = vectors.shape[1]
    print(f"📐 Размерность: {dim}")

    # 2. Подключаемся к LanceDB
    LANCEDB_DIR.mkdir(exist_ok=True)
    db = lancedb.connect(str(LANCEDB_DIR))

    try:
        db.drop_table(TABLE_NAME)
        print(f"🗑 Старая таблица '{TABLE_NAME}' удалена")
    except Exception:
        pass

    # 3. Формируем записи
    records = []
    for i in range(n):
        meta = metadata[i]
        vec = vectors[i].astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm

        uid = meta.get("user_id")
        records.append({
            "vector": vec.tolist(),
            "text": meta.get("text", ""),
            "category": meta.get("category", "general"),
            "source": meta.get("source", "reflection"),
            "user_id": uid if uid is not None else -1,
            "timestamp": meta.get("timestamp", ""),
        })

    print(f"📝 Подготовлено: {len(records)}")

    # 4. Создаём таблицу и вставляем
    schema = pa.schema([
        pa.field("vector", pa.list_(pa.float32(), dim)),
        pa.field("text", pa.string()),
        pa.field("category", pa.string()),
        pa.field("source", pa.string()),
        pa.field("user_id", pa.int64()),
        pa.field("timestamp", pa.string()),
    ])
    table = db.create_table(TABLE_NAME, schema=schema)
    table.add(records)
    print(f"✅ LanceDB: {table.count_rows()} записей")

    # 5. Бэкап FAISS
    BACKUP_DIR.mkdir(exist_ok=True)
    shutil.copy2(FAISS_INDEX, BACKUP_DIR / "faiss_index.bin.bak")
    shutil.copy2(FAISS_META, BACKUP_DIR / "faiss_metadata.json.bak")
    print(f"💾 Бэкап FAISS: {BACKUP_DIR}")

    print()
    print("=" * 60)
    print("✅ МИГРАЦИЯ ЗАВЕРШЕНА")
    print("=" * 60)
    print()
    print("Что дальше:")
    print("  1. Замени импорты faiss_memory → lancedb_memory в файлах")
    print("     (см. список в чате).")
    print("  2. Перезапусти бота: python run.py")
    print("  3. Старые файлы FAISS оставь на неделю — на случай отката.")
    return True


if __name__ == "__main__":
    if "--apply" not in sys.argv:
        print("Это DRY-RUN. Ничего не изменено.")
        print("Для применения: python migrate_faiss_to_lancedb.py --apply")
        print()
        # Покажем что есть
        if FAISS_INDEX.exists():
            idx = faiss.read_index(str(FAISS_INDEX))
            print(f"FAISS-индекс: {idx.ntotal} векторов")
        if FAISS_META.exists():
            md = json.loads(FAISS_META.read_text(encoding="utf-8"))
            print(f"FAISS-metadata: {len(md)} записей")
        sys.exit(0)

    ok = migrate()
    sys.exit(0 if ok else 1)