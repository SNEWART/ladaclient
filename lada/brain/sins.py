# sins.py — 14 шкал: 7 грехов + 7 добродетелей

import json
from pathlib import Path

FILE = Path("data") / "sins.json"
FILE.parent.mkdir(exist_ok=True)

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


def _load() -> dict:
    if FILE.exists():
        try:
            data = json.loads(FILE.read_text(encoding="utf-8"))
            base = _default()
            base.update({k: float(v) for k, v in data.items() if k in SINS})
            return base
        except Exception:
            return _default()
    return _default()


def _save(data: dict):
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_state() -> dict:
    return _load()


def apply_deltas(deltas: dict):
    state = _load()
    for k, v in deltas.items():
        if k not in SINS:
            continue
        try:
            dv = float(v)
        except (TypeError, ValueError):
            continue
        dv = max(-3.0, min(3.0, dv))
        state[k] = max(0.0, min(100.0, state[k] + dv))
    _save(state)


def regress_toward_neutral(factor: float = 0.5):
    """Медленное возвращение к 50. Вызывать раз в час."""
    state = _load()
    for k in SINS:
        state[k] += (NEUTRAL - state[k]) * (factor / 100.0)
    _save(state)


def describe() -> str:
    """Короткое описание доминирующих отклонений для промпта."""
    state = _load()
    deviations = [(k, v - NEUTRAL) for k, v in state.items() if abs(v - NEUTRAL) > 10]
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