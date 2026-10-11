# users_names.py — имена пользователей (SQLite)

from datetime import datetime

from lada.core.db import get_conn, ensure_user, Transaction


def update(user_id: int, username: str = "", first_name: str = "",
           last_name: str = "", phone: str = ""):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        ensure_user(user_id)

        sets = []
        params = []
        if username:
            sets.append("username = ?")
            params.append(username.lstrip("@"))
        if first_name:
            sets.append("first_name = ?")
            params.append(first_name)
        if last_name:
            sets.append("last_name = ?")
            params.append(last_name)
        if phone:
            sets.append("phone = ?")
            params.append(phone)

        sets.append("updated = ?")
        params.append(ts)
        params.append(user_id)

        conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?",
            params,
        )


def get(user_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT username, first_name, last_name, phone, updated "
        "FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return {}
    return {
        "username": row["username"] or "",
        "first_name": row["first_name"] or "",
        "last_name": row["last_name"] or "",
        "phone": row["phone"] or "",
        "updated": row["updated"] or "",
    }


def display_name(user_id: int) -> str:
    data = get(user_id)
    if data.get("username"):
        return "@" + data["username"]

    parts = []
    if data.get("first_name"):
        parts.append(data["first_name"])
    if data.get("last_name"):
        parts.append(data["last_name"])
    if parts:
        return " ".join(parts)

    return f"ID {user_id}"


def all_names() -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id, username, first_name, last_name, phone, updated "
        "FROM users"
    ).fetchall()
    out = {}
    for r in rows:
        out[str(r["user_id"])] = {
            "username": r["username"] or "",
            "first_name": r["first_name"] or "",
            "last_name": r["last_name"] or "",
            "phone": r["phone"] or "",
            "updated": r["updated"] or "",
        }
    return out