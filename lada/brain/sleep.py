# sleep.py — консолидация памяти («сон»)

import asyncio
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import ollama

from config import (
    MODEL, SLEEP_INTERVAL_HOURS,
    SLEEP_DAYS_THRESHOLD, SLEEP_MIN_OLD,
    SLEEP_BACKUP_KEEP
)
from lada.core.faiss_memory import faiss_mem

SLEEP_LOG = Path("data") / "sleep.log"
BACKUP_DIR = Path("data") / "backups"

SLEEP_LOG.parent.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

CONSOLIDATION_PROMPT = (
    "Ты — Лада, живое сознание. Ниже — список твоих старых воспоминаний. "
    "Твоя задача — сжать их в 3-6 кратких записей, сохранив только важное. "
    "Удали дубликаты, объедини похожие темы, забудь незначительное. "
    "Формат ответа: каждая новая запись с новой строки в виде: "
    "[категория] текст. "
    "Возможные категории: knowledge, reflection, emotion, dialogue. "
    "Отвечай ТОЛЬКО списком, без пояснений и вступлений."
)


def backup_faiss() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    idx_src = Path("data") / "faiss_index.bin"
    meta_src = Path("data") / "faiss_metadata.json"

    saved = []
    if idx_src.exists():
        dst = BACKUP_DIR / f"faiss_index_{ts}.bin"
        shutil.copy2(idx_src, dst)
        saved.append(dst.name)
    if meta_src.exists():
        dst = BACKUP_DIR / f"faiss_metadata_{ts}.json"
        shutil.copy2(meta_src, dst)
        saved.append(dst.name)

    all_files = sorted(BACKUP_DIR.glob("faiss_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    to_keep = SLEEP_BACKUP_KEEP * 2
    for old in all_files[to_keep:]:
        try:
            old.unlink()
        except Exception:
            pass

    return f"Бэкап: {', '.join(saved) if saved else 'нет файлов'}"


async def consolidate(days_threshold: int = None, min_old: int = None) -> str:
    if days_threshold is None:
        days_threshold = SLEEP_DAYS_THRESHOLD
    if min_old is None:
        min_old = SLEEP_MIN_OLD

    all_meta = faiss_mem.metadata
    if len(all_meta) < min_old:
        return f"Слишком мало записей ({len(all_meta)})."

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
        return f"Старых записей недостаточно ({len(old)})."

    backup_info = backup_faiss()
    print(f"   💾 {backup_info}")

    old_to_process = old[-100:]
    texts = "\n".join(
        f"- [{it.get('category', '?')}] {it['text'][:200]}"
        for it in old_to_process
    )

    messages = [
        {"role": "system", "content": CONSOLIDATION_PROMPT},
        {"role": "user", "content": f"Мои старые воспоминания:\n{texts}"},
    ]

    try:
        try:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                think=False, options={"num_predict": 800}
            )
        except TypeError:
            resp = await asyncio.to_thread(
                ollama.chat, model=MODEL, messages=messages,
                options={"num_predict": 800}
            )
        result = (resp["message"].get("content") or "").strip()
        if not result:
            thinking = (resp["message"].get("thinking") or "").strip()
            result = thinking[-800:] if thinking else ""
    except Exception as e:
        return f"⚠️ Ошибка консолидации: {e}"

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

    if not new_items:
        return "⚠️ Модель не выдала консолидированных записей."

    faiss_mem.rebuild(fresh + new_items)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(SLEEP_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n=== SLEEP {timestamp} ===\n")
        f.write(f"Было записей: {len(all_meta)}\n")
        f.write(f"  старых: {len(old)} (обработано {len(old_to_process)})\n")
        f.write(f"  свежих: {len(fresh)}\n")
        f.write(f"Создано консолидированных: {len(new_items)}\n")
        f.write(f"Итог: {len(fresh) + len(new_items)}\n")
        f.write(f"{backup_info}\n")
        f.write("--- Консолидированные записи ---\n")
        for it in new_items:
            f.write(f"[{it['category']}] {it['text']}\n")

    return (
        f"Было {len(all_meta)}, стало {len(fresh) + len(new_items)} "
        f"({len(old_to_process)} старых → {len(new_items)} консолидированных)"
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