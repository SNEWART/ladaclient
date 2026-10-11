# migrate_to_sqlite.py — миграция состояния из JSON в SQLite.
#
# Запуск:
#   python migrate_to_sqlite.py           # dry-run, показать что будет
#   python migrate_to_sqlite.py --apply   # применить
#
# Что делает:
#   1. Создаёт все таблицы (init_db)
#   2. Читает все data/*.json и раскладывает по таблицам
#   3. Читает data/users/*.json → messages
#   4. Читает data/working_memory.md → working_memory
#   5. НЕ удаляет исходные JSON (можешь проверить)
#
# После проверки — можешь удалить JSON файлы руками.

import json
import sys
from datetime import datetime
from pathlib import Path

DATA = Path("data")
USERS_DIR = DATA / "users"

# Модули проекта
sys.path.insert(0, str(Path(__file__).parent))

from lada.core.db import init_db, get_conn, Transaction, ensure_user


def _load_json(name: str):
    p = DATA / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"   ⚠️ {name}: {e}")
        return None


def _load_text(name: str) -> str:
    p = DATA / name
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def migrate():
    print("=" * 60)
    print("МИГРАЦИЯ JSON → SQLite")
    print("=" * 60)

    init_db()
    print("✅ Таблицы созданы\n")

    conn = get_conn()

    # ─── users (базовая инфа + first_seen + last_activity + city) ───

    users_names = _load_json("users_names.json") or {}
    first_seen = _load_json("first_seen.json") or {}
    last_activity = _load_json("last_activity.json") or {}
    user_cities = _load_json("user_city.json") or {}

    all_uids = set()
    for d in (users_names, first_seen, last_activity, user_cities):
        for k in d.keys():
            try:
                all_uids.add(int(k))
            except (TypeError, ValueError):
                continue

    # Собираем всех из истории диалогов
    if USERS_DIR.exists():
        for f in USERS_DIR.glob("*.json"):
            try:
                all_uids.add(int(f.stem))
            except (TypeError, ValueError):
                continue

    print(f"📊 Найдено пользователей: {len(all_uids)}")

    with Transaction() as c:
        for uid in all_uids:
            ensure_user(uid)

            key = str(uid)
            info = users_names.get(key, {}) or {}
            fs = first_seen.get(key)
            la = last_activity.get(key)
            city = user_cities.get(key)

            c.execute(
                "UPDATE users SET username = ?, first_name = ?, "
                "last_name = ?, phone = ?, city = ?, first_seen = ?, "
                "last_activity = ?, updated = ? WHERE user_id = ?",
                (
                    info.get("username"),
                    info.get("first_name"),
                    info.get("last_name"),
                    info.get("phone"),
                    city,
                    fs,
                    la,
                    info.get("updated") or datetime.now().isoformat(timespec="seconds"),
                    uid,
                ),
            )

    print("   ✓ users\n")

    # ─── user_state: trust, intimacy, max_level ───

    trust_data = _load_json("user_trust.json") or {}

    with Transaction() as c:
        for uid in all_uids:
            key = str(uid)
            entry = trust_data.get(key, {}) or {}
            t = entry.get("trust")
            i = entry.get("intimacy")
            ml = entry.get("max_level")
            if t is not None or i is not None or ml is not None:
                c.execute(
                    "UPDATE user_state SET trust = ?, intimacy = ?, "
                    "max_level_override = ?, updated = ? WHERE user_id = ?",
                    (t, i, ml,
                     entry.get("updated") or datetime.now().isoformat(timespec="seconds"),
                     uid),
                )

    print("   ✓ trust/intimacy\n")

    # ─── user_state: status ───

    rel = _load_json("relationships.json") or {}

    with Transaction() as c:
        for uid in all_uids:
            entry = rel.get(str(uid), {}) or {}
            if not entry:
                continue
            c.execute(
                "UPDATE user_state SET status = ?, status_since = ?, "
                "status_note = ?, updated = ? WHERE user_id = ?",
                (
                    entry.get("status", "neutral"),
                    entry.get("since"),
                    entry.get("note", ""),
                    entry.get("updated") or datetime.now().isoformat(timespec="seconds"),
                    uid,
                ),
            )

    print("   ✓ statuses\n")

    # ─── user_state: proposals ───

    proposals = _load_json("proposals.json") or {}

    with Transaction() as c:
        for uid in all_uids:
            entry = proposals.get(str(uid), {}) or {}
            if not entry:
                continue
            rej = entry.get("last_rejection") or {}
            c.execute(
                "UPDATE user_state SET "
                "pending_proposal = ?, pending_since = ?, "
                "last_lover_proposal = ?, last_husband_proposal = ?, "
                "last_rejection_which = ?, last_rejection_at = ?, "
                "accepted = ?, accepted_at = ?, "
                "updated = ? WHERE user_id = ?",
                (
                    entry.get("pending"),
                    entry.get("pending_since"),
                    entry.get("last_lover_proposal"),
                    entry.get("last_husband_proposal"),
                    rej.get("which"),
                    rej.get("at"),
                    entry.get("accepted"),
                    entry.get("accepted_at"),
                    datetime.now().isoformat(timespec="seconds"),
                    uid,
                ),
            )

    print("   ✓ proposals\n")

    # ─── lada_state: mood ───

    mood_data = _load_json("mood.json") or {}
    if mood_data:
        with Transaction() as c:
            c.execute(
                "INSERT INTO lada_state (key, value, updated) VALUES "
                "('mood', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated = excluded.updated",
                (json.dumps(mood_data, ensure_ascii=False),
                 mood_data.get("updated") or datetime.now().isoformat(timespec="seconds")),
            )
        print(f"   ✓ mood: {mood_data.get('mood')}\n")

    # ─── lada_state: sins ───

    sins_data = _load_json("sins.json") or {}
    if sins_data:
        with Transaction() as c:
            c.execute(
                "INSERT INTO lada_state (key, value, updated) VALUES "
                "('sins', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated = excluded.updated",
                (json.dumps(sins_data, ensure_ascii=False),
                 datetime.now().isoformat(timespec="seconds")),
            )
        print(f"   ✓ sins: {len(sins_data)} шкал\n")

    # ─── unread ───

    unread_data = _load_json("unread.json") or {}

    with Transaction() as c:
        for uid in all_uids:
            entry = unread_data.get(str(uid), {}) or {}
            pending = entry.get("pending", []) or []
            for item in pending:
                c.execute(
                    "INSERT INTO unread (user_id, text, ts) VALUES (?, ?, ?)",
                    (uid, item.get("text", "")[:500], item.get("ts", "")),
                )
            if entry.get("last_unread_at") or entry.get("last_return_attempt_at"):
                c.execute(
                    "INSERT INTO unread_meta (user_id, last_unread_at, "
                    "last_return_attempt_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "last_unread_at = excluded.last_unread_at, "
                    "last_return_attempt_at = excluded.last_return_attempt_at",
                    (uid, entry.get("last_unread_at"),
                     entry.get("last_return_attempt_at")),
                )

    print("   ✓ unread\n")

    # ─── cooldowns ───

    cd = _load_json("cooldown.json") or {}

    with Transaction() as c:
        for uid in all_uids:
            val = cd.get(str(uid))
            if val:
                c.execute(
                    "INSERT INTO cooldowns (user_id, touched_at) VALUES (?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET touched_at = excluded.touched_at",
                    (uid, val),
                )

    print("   ✓ cooldowns\n")

    # ─── photo_history ───

    ph = _load_json("photo_history.json") or {}

    with Transaction() as c:
        for uid in all_uids:
            entry = ph.get(str(uid), {}) or {}
            if not entry:
                continue
            c.execute(
                "INSERT INTO photo_history "
                "(user_id, last_photo_msg_count, total_photos, "
                "recent_prompts, updated) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "last_photo_msg_count = excluded.last_photo_msg_count, "
                "total_photos = excluded.total_photos, "
                "recent_prompts = excluded.recent_prompts, "
                "updated = excluded.updated",
                (
                    uid,
                    entry.get("last_photo_msg_count", 0),
                    entry.get("total_photos", 0),
                    json.dumps(entry.get("recent_prompts", []), ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    print("   ✓ photo_history\n")

    # ─── messages (data/users/*.json) ───

    msg_count = 0
    if USERS_DIR.exists():
        with Transaction() as c:
            for f in USERS_DIR.glob("*.json"):
                try:
                    uid = int(f.stem)
                except (TypeError, ValueError):
                    continue
                try:
                    history = json.loads(f.read_text(encoding="utf-8"))
                except Exception as e:
                    print(f"   ⚠️ {f.name}: {e}")
                    continue
                for m in history:
                    role = m.get("role", "?")
                    content = m.get("content", "")
                    ts = m.get("timestamp") or datetime.now().isoformat(timespec="seconds")
                    c.execute(
                        "INSERT INTO messages (user_id, role, content, ts) "
                        "VALUES (?, ?, ?, ?)",
                        (uid, role, content, ts),
                    )
                    msg_count += 1

    print(f"   ✓ messages: {msg_count} шт.\n")

    # ─── working_memory.md ───

    wm_text = _load_text("working_memory.md")
    if wm_text:
        with Transaction() as c:
            c.execute(
                "INSERT INTO working_memory (user_id, content, updated) "
                "VALUES (0, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET content = excluded.content, "
                "updated = excluded.updated",
                (wm_text, datetime.now().isoformat(timespec="seconds")),
            )
        print(f"   ✓ working_memory: {len(wm_text)} симв.\n")

    # ─── Проверка ───

    print("=" * 60)
    print("ПРОВЕРКА")
    print("=" * 60)

    for table in ("users", "user_state", "messages", "unread",
                  "cooldowns", "photo_history", "lada_state",
                  "working_memory"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"   {table:20s}: {n}")

    print()
    print("✅ Миграция завершена.")
    print("   Исходные JSON-файлы НЕ удалены.")
    print("   Проверь работу бота, потом удали их вручную (см. README ниже).")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    if not apply:
        print("Это DRY-RUN. Ничего не будет изменено.")
        print("Для применения: python migrate_to_sqlite.py --apply")
        print()
        # Всё равно покажем что БУДЕТ сделано
        print("Будет прочитано из data/:")
        for f in DATA.glob("*.json"):
            print(f"   • {f.name}")
        if USERS_DIR.exists():
            print(f"   • users/*.json ({len(list(USERS_DIR.glob('*.json')))} файлов)")
        print("   • working_memory.md")
        sys.exit(0)

    migrate()