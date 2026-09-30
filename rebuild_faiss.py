# rebuild_faiss.py — БЕЗОПАСНАЯ пересборка FAISS индекса
#
# Что делает:
#   1. Читает data/faiss_metadata.json (НЕ трогает его)
#   2. Если записей меньше 100 — восстанавливает из последнего backup
#   3. Строит индекс батчами, сохраняет прогресс каждые 16 записей
#   4. При прерывании (Ctrl+C) — сохраняет и можно продолжить
#   5. В конце переименовывает faiss_index.partial → faiss_index.bin
#
# Запуск:
#   python rebuild_faiss.py           # продолжить/начать
#   python rebuild_faiss.py --reset   # сбросить прогресс и начать заново
#   python rebuild_faiss.py --restore # принудительно восстановить metadata из backup
#   python rebuild_faiss.py --yes     # не спрашивать подтверждения

import json
import sys
import time
import shutil
from pathlib import Path

DATA_DIR = Path("data")
BACKUPS_DIR = DATA_DIR / "backups"
META_PATH = DATA_DIR / "faiss_metadata.json"
INDEX_PATH = DATA_DIR / "faiss_index.bin"
PARTIAL_INDEX = DATA_DIR / "faiss_index.partial"
PROGRESS_FILE = DATA_DIR / "faiss_rebuild_progress.json"

EMBED_BATCH = 16
AUTO_YES = "--yes" in sys.argv


def log(s):
    print(s, flush=True)


def section(title):
    log("")
    log("=" * 60)
    log(title)
    log("=" * 60)


def find_latest_metadata_backup() -> Path | None:
    if not BACKUPS_DIR.exists():
        return None
    candidates = sorted(
        BACKUPS_DIR.glob("faiss_metadata_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def find_latest_index_backup() -> Path | None:
    if not BACKUPS_DIR.exists():
        return None
    candidates = sorted(
        BACKUPS_DIR.glob("faiss_index_*.bin"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def restore_metadata_from_backup(force: bool = False) -> bool:
    """Восстанавливает metadata из последнего backup, если нужно."""
    current_count = 0
    if META_PATH.exists():
        try:
            current_count = len(json.loads(META_PATH.read_text(encoding="utf-8")))
        except Exception:
            current_count = 0

    if current_count >= 100 and not force:
        log(f"📄 metadata в порядке: {current_count} записей (восстановление не нужно)")
        return True

    backup = find_latest_metadata_backup()
    if not backup:
        log(f"❌ Backups не найдены в {BACKUPS_DIR}")
        return False

    try:
        backup_data = json.loads(backup.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"❌ Не удалось прочитать backup: {e}")
        return False

    log(f"💾 Найден backup: {backup.name}")
    log(f"   Записей в backup: {len(backup_data)}")
    log(f"   Текущий metadata: {current_count} записей")

    if len(backup_data) < current_count and not force:
        log("   Backup меньше текущего — не восстанавливаю.")
        return True

    if META_PATH.exists():
        broken = META_PATH.with_suffix(".json.broken")
        shutil.copy2(META_PATH, broken)
        log(f"   Сломанный metadata сохранён: {broken.name}")

    shutil.copy2(backup, META_PATH)
    log(f"   ✅ Восстановлено {len(backup_data)} записей в {META_PATH}")
    return True


def restore_old_index():
    """Копирует старый .bin-индекс, чтобы бот мог работать, пока идёт пересборка."""
    backup = find_latest_index_backup()
    if not backup:
        return
    shutil.copy2(backup, INDEX_PATH)
    log(f"💾 Восстановлен рабочий индекс из backup: {backup.name}")
    log(f"   (L2-метрика — не идеальна, но бот запустится)")


def test_embedding_speed() -> float:
    section("ПРОВЕРКА СКОРОСТИ ЭМБЕДДИНГА")
    try:
        import ollama
        from config import EMBEDDING_MODEL
    except Exception as e:
        log(f"❌ Импорт: {e}")
        return -1

    log(f"Модель: {EMBEDDING_MODEL}")
    log(f"Прогрев (первый запрос может быть долгим)...")

    try:
        t = time.time()
        r = ollama.embed(model=EMBEDDING_MODEL, input="тест", keep_alive="30m")
        warm = time.time() - t
        dim = len(r["embeddings"][0])
        log(f"  Прогрев: {warm:.2f} сек, размерность: {dim}")
    except Exception as e:
        log(f"❌ Ollama не отвечает: {e}")
        log(f"   Проверь: ollama list")
        return -1

    t = time.time()
    r = ollama.embed(model=EMBEDDING_MODEL, input="второй тест для замера", keep_alive="30m")
    single = time.time() - t
    log(f"  Один текст: {single:.2f} сек")

    t = time.time()
    r = ollama.embed(model=EMBEDDING_MODEL, input=["а"] * 8, keep_alive="30m")
    batch8 = time.time() - t
    log(f"  Батч из 8: {batch8:.2f} сек ({batch8/8:.2f} сек на текст)")

    return single


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"processed": 0}


def save_progress(processed: int, total: int):
    PROGRESS_FILE.write_text(
        json.dumps({"processed": processed, "total": total}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}с"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}м {s}с"
    h, m = divmod(m, 60)
    return f"{h}ч {m}м"


def rebuild(force_reset: bool = False):
    section("ПЕРЕСБОРКА FAISS ИНДЕКСА")

    if force_reset:
        for f in (PARTIAL_INDEX, PROGRESS_FILE):
            if f.exists():
                f.unlink()
        log("🔄 Прогресс сброшен (--reset)")

    if not META_PATH.exists():
        log(f"❌ {META_PATH} не найден")
        return False

    try:
        metadata = json.loads(META_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"❌ Не удалось прочитать metadata: {e}")
        return False

    total = len(metadata)
    log(f"Записей в metadata: {total}")

    if total == 0:
        log("❌ metadata пуст. Нечего пересобирать.")
        return False

    if total < 100:
        log(f"⚠️  Всего {total} записей — это подозрительно мало.")
        log(f"   Запусти: python rebuild_faiss.py --restore")
        return False

    single_speed = test_embedding_speed()
    if single_speed < 0:
        return False

    estimated = total * single_speed / EMBED_BATCH
    log("")
    log(f"⏱  Оценка времени: ~{_fmt_time(estimated)} "
        f"({total} записей, батч {EMBED_BATCH})")

    if estimated > 600 and not AUTO_YES:
        log("")
        log("⚠️  ЭТО ОЧЕНЬ ДОЛГО (>10 минут).")
        log("   Скорее всего, qwen3-embedding работает через CPU.")
        log("")
        log("   Рекомендую сменить модель эмбеддингов на лёгкую:")
        log("     ollama pull bge-m3")
        log('   И в config.py: EMBEDDING_MODEL = "bge-m3"')
        log("")
        ans = input("Продолжить всё равно? [y/N]: ").strip().lower()
        if ans != "y":
            return False

    progress = load_progress()
    processed = progress.get("processed", 0)

    import numpy as np
    import faiss

    index = None
    if PARTIAL_INDEX.exists() and processed > 0:
        try:
            index = faiss.read_index(str(PARTIAL_INDEX))
            log(f"♻️  Продолжаем с записи {processed}/{total}")
            if index.ntotal != processed:
                log(f"   ⚠️  В индексе {index.ntotal}, в прогрессе {processed} — синхронизирую")
                processed = index.ntotal
        except Exception as e:
            log(f"⚠️  Не удалось загрузить partial: {e}")
            index = None
            processed = 0

    if index is None:
        processed = 0
        log(f"🆕 Начинаем с нуля")

    try:
        import ollama
        from config import EMBEDDING_MODEL
    except Exception as e:
        log(f"❌ {e}")
        return False

    start_time = time.time()

    try:
        while processed < total:
            end = min(processed + EMBED_BATCH, total)
            batch = metadata[processed:end]
            texts = [item["text"] for item in batch]

            try:
                resp = ollama.embed(
                    model=EMBEDDING_MODEL,
                    input=texts,
                    keep_alive="30m",
                )
                embs = resp.get("embeddings", [])
            except Exception as e:
                log(f"❌ Ошибка эмбеддинга на {processed}-{end}: {e}")
                log(f"   Сохраняю прогресс и выхожу. Запусти снова.")
                break

            vectors = []
            for e in embs:
                v = np.asarray(e, dtype=np.float32)
                faiss.normalize_L2(v.reshape(1, -1))
                vectors.append(v)

            if not vectors:
                log(f"⚠️  Пустой ответ на {processed}-{end}, прерываю")
                break

            arr = np.vstack(vectors).astype(np.float32)

            if index is None:
                index = faiss.IndexHNSWFlat(
                    arr.shape[1],
                    32,
                    faiss.METRIC_INNER_PRODUCT,
                )
                index.hnsw.efConstruction = 200
                index.hnsw.efSearch = 64
                log(f"   Создан индекс: dim={arr.shape[1]}")

            index.add(arr)
            processed = end

            faiss.write_index(index, str(PARTIAL_INDEX))
            save_progress(processed, total)

            elapsed = time.time() - start_time
            rate = processed / elapsed if elapsed > 0 else 0
            eta = (total - processed) / rate if rate > 0 else 0
            pct = processed * 100 // total

            log(f"   [{processed}/{total}] {pct}% "
                f"прошло {_fmt_time(elapsed)}, "
                f"осталось ~{_fmt_time(eta)}")

    except KeyboardInterrupt:
        log("")
        log("⏸  Прервано пользователем. Прогресс сохранён.")
        log(f"   Обработано: {processed}/{total}")
        log(f"   Продолжить: python rebuild_faiss.py")
        return False

    if processed < total:
        log("")
        log(f"⚠️  Обработано только {processed}/{total}. Запусти снова для продолжения.")
        return False

    section("ЗАВЕРШЕНИЕ")
    if INDEX_PATH.exists():
        INDEX_PATH.unlink()
    PARTIAL_INDEX.rename(INDEX_PATH)
    PROGRESS_FILE.unlink(missing_ok=True)

    log(f"✅ Индекс готов: {INDEX_PATH}")
    log(f"   Записей: {index.ntotal}")

    if hasattr(index, "metric_type"):
        mt = index.metric_type
        if mt == faiss.METRIC_INNER_PRODUCT:
            log("✅ Метрика: Inner Product (правильно)")
        elif mt == faiss.METRIC_L2:
            log("❌ Метрика: L2 (что-то пошло не так)")

    section("ТЕСТ ПОИСКА")
    try:
        test_vec = ollama.embed(model=EMBEDDING_MODEL, input="привет", keep_alive="30m")
        v = np.asarray(test_vec["embeddings"][0], dtype=np.float32)
        faiss.normalize_L2(v.reshape(1, -1))
        v = v.reshape(1, -1)

        distances, indices = index.search(v, 3)
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(metadata):
                continue
            item = metadata[idx]
            log(f"  score={float(dist):.3f} | {item.get('category')} | {item['text'][:70]}")
    except Exception as e:
        log(f"  ⚠️ Тест не удался: {e}")

    log("")
    log("Готово. Запускай run.py.")
    return True


def main():
    args = sys.argv[1:]

    if "--reset" in args:
        rebuild(force_reset=True)
        return

    if "--restore" in args:
        section("ВОССТАНОВЛЕНИЕ ИЗ BACKUP")
        ok = restore_metadata_from_backup(force=True)
        if not ok:
            sys.exit(1)
        if INDEX_PATH.exists() is False:
            log("")
            log("⚠️  Старый индекс отсутствует.")
            log("   Восстанавливаю L2-индекс из backup, чтобы бот работал,")
            log("   пока идёт пересборка.")
            restore_old_index()
        log("")
        log("Теперь можно запустить: python rebuild_faiss.py")
        return

    section("ПРОВЕРКА METADATA")
    ok = restore_metadata_from_backup(force=False)
    if not ok:
        sys.exit(1)

    rebuild(force_reset=False)


if __name__ == "__main__":
    main()