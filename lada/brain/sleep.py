# sleep.py — консолидация памяти («сон»).
#
# Бэкап теперь целиком копирует data/lancedb/, а не старые FAISS-файлы.

import asyncio
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import ollama

from config import (
    MODEL, SLEEP_INTERVAL_HOURS,
    SLEEP_DAYS_THRESHOLD, SLEEP_MIN_OLD,
    SLEEP_BACKUP_KEEP,
)
from lada.core.lancedb_memory import lance_mem as faiss_mem

SLEEP_LOG = Path("data") / "sleep.log"
BACKUP_DIR = Path("data") / "backups"
LANCEDB_DIR = Path("data") / "lancedb"

SLEEP_LOG.parent.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

CONSOLIDATION_BATCH = 25
MAX_BATCHES_PER_RUN = 4


CONSOLIDATION_PROMPT = (
    "Ты — консолидатор памяти Лады. Ниже список её старых записей.\n"
    "Сожми их в 3-6 кратких записей, сохранив ТОЛЬКО важное.\n"
    "Удали дубликаты. Объедини похожие темы. Забудь мелочи.\n\n"
    "ФОРМАТ ОТВЕТА: каждая новая запись с новой строки в виде:\n"
    "[категория] текст\n\n"
    "Категории: knowledge, reflection, emotion, dialogue.\n"
    "Отвечай ТОЛЬКО списком, без пояснений и вступлений."
)


def backup_lancedb() -> str:
    """Копирует всю папку data/lancedb/ в data/backups/lancedb_<ts>/."""
    if not LANCEDB_DIR.exists():
        return "Бэкап: lancedb не найден"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"lancedb_{ts}"
    try:
        shutil.copytree(LANCEDB_DIR, dst)
    except Exception as e:
        return f"Бэкап LanceDB: ошибка {e}"

    # Оставляем последние N бэкапов
    backups = sorted(
        BACKUP_DIR.glob("lancedb_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[SLEEP_BACKUP_KEEP:]:
        try:
            if old.is_dir():
                shutil.rmtree(old)
            else:
                old.unlink()
        except Exception:
            pass

    return f"Бэкап LanceDB: {dst.name}"


async def _consolidate_batch(batch: list) -> list:
    texts = "\n".join(
        f"- [{it.get('category', '?')}] {it['text'][:200]}"
        for it in batch
    )

    messages = [
        {"role": "system", "content": CONSOLIDATION_PROMPT},
        {"role": "user", "content": f"Старые записи:\n{texts}"},
    ]

    try:
        try:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                think=False, options={"num_predict": 600}
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                options={"num_predict": 600}
            )
        result = (resp["message"].get("content") or "").strip()
        if not result:
            thinking = (resp["message"].get("thinking") or "").strip()
            result = thinking[-600:] if thinking else ""
    except Exception as e:
        print(f"   ⚠️ Батч упал: {e}")
        return []

    new_items = []
    for line in result.split("\n"):
        line = line.strip(" -•\t")
        if not line:
            continue
        m = re.match(r'\[(\w+)\]\s*(.+)', line)
        if m:
            cat, txt = m.group(1), m.group(2).strip()
        else:
            cat, txt = "knowledge", line
        if len(txt) < 5:
            continue
        new_items.append({
            "text": txt,
            "category": cat,
            "source": "consolidation",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })
    return new_items


async def consolidate(days_threshold: int = None, min_old: int = None,
                       force: bool = False) -> str:
    if days_threshold is None:
        days_threshold = SLEEP_DAYS_THRESHOLD
    if min_old is None:
        min_old = SLEEP_MIN_OLD

    all_meta = list(faiss_mem.metadata)
    if not all_meta:
        return "⚠️ Память пуста. Нечего консолидировать."

    if force:
        old = sorted(all_meta, key=lambda m: m.get("timestamp", "") or "")
        fresh = []
        if len(old) < 3:
            return f"⚠️ Слишком мало записей ({len(old)}). Нужно ≥3."
    else:
        if len(all_meta) < min_old:
            return f"⚠️ Слишком мало записей ({len(all_meta)})."
        cutoff = datetime.now() - timedelta(days=days_threshold)
        fresh, old = [], []
        for item in all_meta:
            ts = item.get("timestamp")
            try:
                dt = datetime.fromisoformat(ts) if ts else None
            except Exception:
                dt = None
            if dt and dt >= cutoff:
                fresh.append(item)
            else:
                old.append(item)
        if len(old) < min_old:
            return (
                f"💤 Нечего консолидировать: все {len(all_meta)} записей "
                f"свежее {days_threshold} дн. Используй кнопку "
                f"«Сон (форс)» для ручной консолидации."
            )

    backup_info = backup_lancedb()
    print(f"   💾 {backup_info}")

    batches = [
        old[i:i + CONSOLIDATION_BATCH]
        for i in range(0, len(old), CONSOLIDATION_BATCH)
    ]
    process_batches = batches[:MAX_BATCHES_PER_RUN]
    processed_count = sum(len(b) for b in process_batches)
    unprocessed_old = old[processed_count:]

    print(f"   📦 Старых записей: {len(old)}, батчей: {len(batches)} "
          f"(обработаем {len(process_batches)})")

    all_new = []
    for i, batch in enumerate(process_batches, 1):
        print(f"   [{i}/{len(process_batches)}] батч из {len(batch)} записей...")
        new_items = await _consolidate_batch(batch)
        if new_items:
            all_new.extend(new_items)
            print(f"      → {len(new_items)} новых")
        else:
            print(f"      → пусто, оставляю оригиналы")
            unprocessed_old.extend(batch)

    if not all_new:
        return "⚠️ Модель не выдала консолидированных записей."

    faiss_mem.rebuild(fresh + unprocessed_old + all_new)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(SLEEP_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n=== SLEEP {timestamp} "
                f"{'(FORCED)' if force else ''} ===\n")
        f.write(f"Было записей: {len(all_meta)}\n")
        f.write(f"  старых: {len(old)}\n")
        f.write(f"  обработано: {processed_count}\n")
        f.write(f"  отложено: {len(unprocessed_old)}\n")
        f.write(f"  свежих: {len(fresh)}\n")
        f.write(f"Создано: {len(all_new)}\n")
        f.write(f"{backup_info}\n")
        f.write("--- Консолидированные ---\n")
        for it in all_new:
            f.write(f"[{it['category']}] {it['text']}\n")

    return (
        f"Было {len(all_meta)}, стало "
        f"{len(fresh) + len(unprocessed_old) + len(all_new)}. "
        f"Обработано старых: {processed_count} → {len(all_new)}."
    )


async def sleep_loop(interval_hours: int = None):
    if interval_hours is None:
        interval_hours = SLEEP_INTERVAL_HOURS
    await asyncio.sleep(3600)
    while True:
        try:
            print(f"\n💤 [{datetime.now().strftime('%H:%M:%S')}] Запуск «сна»...")
            result = await consolidate()
            print(f"💤 Результат: {result}")
        except Exception as e:
            print(f"⚠️ Ошибка сна: {e}")
        await asyncio.sleep(interval_hours * 3600)