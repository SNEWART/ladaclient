# memory_clean.py — единая утилита памяти: показать мусор, удалить, восстановить.
#
# Запуск:
#   python memory_clean.py              # показать мусор (dry-run)
#   python memory_clean.py --apply      # удалить мусор
#   python memory_clean.py --restore    # восстановить валидные факты из бэкапа
#   python memory_clean.py --restore --apply

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lada.core.lancedb_memory import lance_mem, LANCEDB_DIR


# ─── Что принадлежит Ладе, а не пользователю ───
# Если LLM пишет «у пользователя есть X», где X — атрибут Лады,
# это семантическая ошибка. Ловим по именам и ключевым словам.
LADA_OWNED = (
    "муся", "мусю", "муси", "мусей", "муськ",
    "кошк лады", "кошка лады", "кошку лады",
    "её кошка", "ее кошка", "у лады есть",
)

# Префиксы-пересказ (вырезаются, суть сохраняется).
STRIP_PREFIXES = (
    r'^(?:пользователь|он|собеседник)\s+(?:сказал|написал|сообщил|'
    r'упомянул|рассказал|ответил|поделился|добавил|уточнил),?\s+'
    r'(?:что\s+)?',
    r'^(?:пользователь|он|собеседник)\s+(?:сообщил|упомянул)\s+о\s+',
    r'^у\s+(?:пользователя|него|собеседника)\s+',
)

# Жёсткий мусор — реплики, вопросы, взаимодействия с Ладой.
HARD_REJECT = (
    r'^(?:пользователь|он|собеседник)\s+(?:спросил|поинтересовался|'
    r'задал|пожелал|попросил\s+у|выразил|отреагировал|эмоционально)',
    r'^(?:пользователь|он)\s+написал\s+(?:мне|ей|ладе)',
    r'^(?:пользователь|он)\s+проявляет\s+(?:интерес\s+к\s+ладе|'
    r'заботу\s+о\s+ладе|внимание\s+к\s+ладе)',
    r'^пользователи\s+',
    r'^в\s+(?:этом\s+)?диалоге',
)

BAD_PATTERNS = (
    re.compile(r'\b(?:о|про|к|с)\s+лад[оеыу]\b', re.IGNORECASE),
    re.compile(r'\bладе\b|\bладу\b|\bладой\b', re.IGNORECASE),
    re.compile(r'\bне\s+мешает\s+ли\b', re.IGNORECASE),
)

MAX_LEN = 160
BACKUP_ROOT = Path("data") / "backups"


def normalize(text: str) -> str:
    t = text.strip()
    for pat in STRIP_PREFIXES:
        new = re.sub(pat, '', t, count=1, flags=re.IGNORECASE)
        if new != t:
            t = new.strip()
            if t and t[0].islower():
                t = t[0].upper() + t[1:]
            break
    return t


def is_bad(text: str) -> str | None:
    """Возвращает причину, если запись — мусор."""
    raw = (text or "").strip()
    if not raw:
        return "пусто"
    low = raw.lower()

    # Мусор от Лады-центричности (Муся и пр.)
    if any(k in low for k in LADA_OWNED):
        return "приписывает пользователю атрибут Лады"

    # Жёсткий мусор
    for pat in HARD_REJECT:
        if re.search(pat, low):
            return f"пересказ реплики"

    # Нормализуем и проверяем дальше
    norm = normalize(raw)
    if len(norm) < 10:
        return "коротко"
    if len(norm) > MAX_LEN:
        return f"длинно ({len(norm)})"
    for pat in BAD_PATTERNS:
        if pat.search(norm):
            return "упоминает Ладу"
    if not re.search(r'[а-яёa-z]{4,}', norm.lower()):
        return "нет содержательных слов"
    return None


def cmd_show():
    items = list(lance_mem.metadata)
    print(f"Всего записей в LanceDB: {len(items)}\n")

    bad_obs = []
    for m in items:
        if m.get("category") != "observation":
            continue
        reason = is_bad(m.get("text", ""))
        if reason:
            bad_obs.append((m, reason))

    print(f"Мусорных observations: {len(bad_obs)}\n")
    for m, reason in bad_obs:
        uid = m.get("user_id")
        print(f"  [{reason}] uid={uid} {m.get('text','')[:100]}")

    return bad_obs


def cmd_apply(bad_obs: list):
    if not bad_obs:
        print("Нечего удалять.")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = BACKUP_ROOT / f"lancedb_preclean_{ts}"
    try:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.copytree(LANCEDB_DIR, bk)
        print(f"💾 Бэкап: {bk}")
    except Exception as e:
        print(f"⚠️ Бэкап не удался: {e}")

    deleted = 0
    for m, _ in bad_obs:
        text = (m.get("text") or "").strip()
        uid = m.get("user_id")
        safe = text.replace("'", "''")
        where = (f"text = '{safe}' AND user_id = {int(uid)}"
                 if uid is not None else
                 f"text = '{safe}' AND user_id = -1")
        try:
            lance_mem.table.delete(where)
            deleted += 1
        except Exception as e:
            print(f"  ⚠️ {text[:50]}: {e}")

    lance_mem._invalidate_meta_cache()
    print(f"\n✅ Удалено {deleted} из {len(bad_obs)}")
    print(f"   Осталось всего: {lance_mem.count()}")


def cmd_restore():
    """Восстанавливает из последнего бэкапа только те записи,
    которые проходят НОВЫЙ фильтр, с нормализацией."""
    if not BACKUP_ROOT.exists():
        print("Бэкапов нет.")
        return

    candidates = sorted(
        BACKUP_ROOT.glob("lancedb_obs_clean_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        print("Бэкапов вида lancedb_obs_clean_* нет.")
        return

    bk = candidates[0]
    print(f"📦 Бэкап: {bk}")

    import lancedb
    try:
        db = lancedb.connect(str(bk))
        tbl = db.open_table("memory")
        rows = tbl.to_arrow().to_pylist()
    except Exception as e:
        print(f"❌ Не читается бэкап: {e}")
        return

    backup_obs = [r for r in rows if r.get("category") == "observation"]
    print(f"В бэкапе observations: {len(backup_obs)}")

    current = {
        (m.get("text") or "").strip()
        for m in lance_mem.metadata
        if m.get("category") == "observation"
    }

    plan = []
    for r in backup_obs:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        if is_bad(text):
            continue
        norm = normalize(text)
        if is_bad(norm):
            continue
        if norm in current:
            continue
        plan.append({"norm": norm, "row": r})

    print(f"\nК восстановлению: {len(plan)}\n")
    for p in plan:
        uid = p["row"].get("user_id")
        marker = "✨" if p["norm"] != (p["row"].get("text") or "").strip() else "  "
        print(f"  {marker} uid={uid} {p['norm'][:100]}")

    if "--apply" not in sys.argv:
        print("\nDRY-RUN. Для применения добавь --apply")
        return

    if not plan:
        print("Нечего восстанавливать.")
        return

    restored = 0
    for p in plan:
        r = p["row"]
        uid = r.get("user_id")
        if uid == -1:
            uid = None
        meta = {}
        raw_meta = r.get("meta_json")
        if raw_meta:
            try:
                meta = json.loads(raw_meta)
            except Exception:
                meta = {}
        ok = lance_mem.add(
            p["norm"],
            category="observation",
            source=r.get("source", "dialog"),
            user_id=uid,
            meta=meta or None,
        )
        if ok:
            restored += 1

    print(f"\n✅ Восстановлено {restored} из {len(plan)}")
    print(f"   Всего в LanceDB: {lance_mem.count()}")


def main():
    if "--restore" in sys.argv:
        cmd_restore()
        return

    bad = cmd_show()
    if "--apply" in sys.argv:
        cmd_apply(bad)
    else:
        print("\nDRY-RUN. Для удаления: python memory_clean.py --apply")


if __name__ == "__main__":
    main()