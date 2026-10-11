# user_city.py — города пользователей (SQLite)

from lada.core.db import get_conn, ensure_user, Transaction


def get_city(user_id: int) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT city FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row and row["city"]:
        return str(row["city"]).strip()
    return None


def set_city(user_id: int, city: str | None):
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "UPDATE users SET city = ? WHERE user_id = ?",
            (city.strip() if city else None, user_id),
        )


def all_cities() -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT user_id, city FROM users WHERE city IS NOT NULL"
    ).fetchall()
    return {str(r["user_id"]): r["city"] for r in rows}