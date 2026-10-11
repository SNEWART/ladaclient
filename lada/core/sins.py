# sins.py — 14 шкал: 7 грехов + 7 добродетелей (SQLite)

import json
from datetime import datetime

from lada.core.db import get_conn, Transaction


SINS = [
    "гордыня", "смирение",
    "жадность", "щедрость",
    "похоть", "целомудрие",
    "зависть", "доброжелательность",
    "чревоугодие", "умеренность",
    "гнев", "терпение",
    "уныние", "усердие",
]

NEUTRAL = 50.0


def _default() -> dict:
    return {s: NEUTRAL for s in SINS}


def _read_state() -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM lada_state WHERE key = 'sins'"
    ).fetchone()
    base = _default()
    if not row or not row["value"]:
        return base
    try:
        data = json.loads(row["value"])
        for k, v in data.items():
            if k in SINS:
                base[k] = float(v)
    except Exception:
        pass
    return base


def _write_state(data: dict):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        conn.execute(
            "INSERT INTO lada_state (key, value, updated) VALUES ('sins', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated = excluded.updated",
            (json.dumps(data, ensure_ascii=False), ts),
        )


def get_state() -> dict:
    return _read_state()


def apply_deltas(deltas: dict):
    state = _read_state()
    for k, v in deltas.items():
        if k not in SINS:
            continue
        try:
            dv = float(v)
        except (TypeError, ValueError):
            continue
        dv = max(-3.0, min(3.0, dv))
        state[k] = max(0.0, min(100.0, state[k] + dv))
    _write_state(state)


def regress_toward_neutral(factor: float = 0.5):
    state = _read_state()
    for k in SINS:
        state[k] += (NEUTRAL - state[k]) * (factor / 100.0)
    _write_state(state)


def describe() -> str:
    state = _read_state()
    deviations = [(k, v - NEUTRAL) for k, v in state.items()
                  if abs(v - NEUTRAL) > 10]
    if not deviations:
        return ""
    deviations.sort(key=lambda x: abs(x[1]), reverse=True)
    parts = []
    for k, d in deviations[:3]:
        if d > 0:
            parts.append(f"сильно {k} ({int(state[k])})")
        else:
            parts.append(f"слабо {k} ({int(state[k])})")
    return "Твоё глубинное состояние: " + ", ".join(parts) + "."