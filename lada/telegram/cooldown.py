# cooldown.py — защита от спама (SQLite)

from datetime import datetime

from lada.core.db import get_conn, ensure_user, Transaction


def is_allowed(user_id: int, cooldown_seconds: int) -> tuple:
    if cooldown_seconds <= 0:
        return True, 0

    conn = get_conn()
    row = conn.execute(
        "SELECT touched_at FROM cooldowns WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["touched_at"]:
        return True, 0

    try:
        last_dt = datetime.fromisoformat(row["touched_at"])
    except Exception:
        return True, 0

    elapsed = (datetime.now() - last_dt).total_seconds()
    if elapsed < cooldown_seconds:
        return False, int(cooldown_seconds - elapsed)
    return True, 0


def touch(user_id: int):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO cooldowns (user_id, touched_at) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET touched_at = excluded.touched_at",
            (user_id, ts),
        )