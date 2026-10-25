# search_agent.py — решает, нужен ли поиск, и формирует КОНКРЕТНЫЙ запрос.
#
# FIX: короткий, конкретный промпт. Раньше модель копировала слова
# из анти-промпта («Лада, Пользователь, диалог, эмоции»).
# Теперь — минимум примеров, максимум конкретики.
# FIX: retry с минимальным промптом — если первый парс упал.

import json
import re
from datetime import datetime

import ollama

from config import (
    SEARCH_AGENT_ENABLED, SEARCH_AGENT_MODEL,
    SEARCH_AGENT_MIN_MSG_LEN,
)


_PROMPT = (
    "Реши, нужен ли поиск в интернете, чтобы ответить на сообщение.\n"
    "\n"
    "ИСКАТЬ ТОЛЬКО:\n"
    "  — новости, погода, курсы валют\n"
    "  — характеристики товаров, цены, даты выхода\n"
    "  — публичные факты (знаменитости, компании, страны)\n"
    "  — явная просьба: «погугли», «найди», «поищи»\n"
    "\n"
    "НЕ ИСКАТЬ:\n"
    "  — приветствия и светские фразы\n"
    "  — эмоции, чувства, философию\n"
    "  — про собеседника, про диалог, про Ладу\n"
    "  — если неясно, о чём речь\n"
    "\n"
    "Если search=true — сформулируй конкретный запрос 3-10 слов.\n"
    "Не «новости», а «новости США выборы октябрь 2026».\n"
    "Не «характеристики ноута», а «MacBook Neo 2026 характеристики».\n"
    "\n"
    "Верни JSON:\n"
    '{"search": true|false, "query": "запрос или пустая строка", '
    '"reason": "кратко"}'
)


_available_cache = None

_MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _enrich_query(query: str) -> str:
    low = query.lower()
    markers = [
        "2024", "2025", "2026", "2027",
        "сегодня", "вчера", "завтра",
        "январ", "феврал", "март", "апрел", "май", "мая",
        "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр",
    ]
    if any(m in low for m in markers):
        return query
    today = datetime.now()
    date_part = f"{today.day} {_MONTHS_RU[today.month]} {today.year}"
    return f"{query} {date_part}"


def available() -> bool:
    global _available_cache
    if not SEARCH_AGENT_ENABLED:
        return False
    if _available_cache is not None:
        return _available_cache
    try:
        ollama.show(SEARCH_AGENT_MODEL)
        _available_cache = True
    except Exception as e:
        _available_cache = False
        print(f"   ⚠️ search_agent: модель '{SEARCH_AGENT_MODEL}' "
              f"не найдена ({e})")
    return _available_cache


def _extract_json(text: str):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def _build_user_block(history: list, current_msg: str) -> str:
    lines = []
    if history:
        lines.append("Последние сообщения:")
        for m in history[-4:]:
            role = "Он" if m.get("role") == "user" else "Я"
            content = (m.get("content") or "").strip()[:200]
            if content:
                lines.append(f"  {role}: {content}")
        lines.append("")

    lines.append("Текущее сообщение:")
    lines.append(f"  {current_msg[:400]}")
    return "\n".join(lines)


def _looks_like_self_topic(query: str) -> bool:
    low = query.lower()
    bad_markers = [
        "лада", "лады", "ладе", "ладу", "ладо",
        "пользователь", "собеседник",
        "диалог", "разговор", "мы обсуждали",
        "как прошла", "как спалось", "как дела у",
        "эмоции", "чувства", "отношения",
    ]
    return any(m in low for m in bad_markers)


def _looks_like_garbage(query: str) -> bool:
    """Запрос-мусор: слишком короткий, длинный, или сплошные общие слова."""
    q = query.strip()
    if len(q) < 3 or len(q) > 120:
        return True
    # Все слова стоп-слова?
    words = [w for w in re.findall(r'\w{3,}', q.lower())]
    if not words:
        return True
    stop = {"что", "как", "где", "когда", "почему", "зачем",
            "это", "есть", "быть", "можно", "нужно",
            "the", "and", "for", "with", "that"}
    real = [w for w in words if w not in stop]
    return len(real) < 1


def _call_model(system: str, user: str, num_predict: int = 200):
    try:
        try:
            resp = ollama.chat(
                model=SEARCH_AGENT_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                format="json",
                think=False,
                keep_alive="5m",
                options={"temperature": 0.0, "num_predict": num_predict},
            )
        except TypeError:
            resp = ollama.chat(
                model=SEARCH_AGENT_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                think=False,
                keep_alive="5m",
                options={"temperature": 0.0, "num_predict": num_predict},
            )
    except Exception as e:
        print(f"   ⚠️ search_agent: {e}")
        return None

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if isinstance(data, dict):
        return data
    print(f"   ⚠️ search_agent: не распарсилось: {content[:120]!r}")
    return None


def decide_search(user_msg: str, history: list = None) -> dict:
    default = {"search": False, "query": "", "reason": "disabled/unavailable"}

    if not SEARCH_AGENT_ENABLED:
        return default
    if not user_msg or len(user_msg.strip()) < SEARCH_AGENT_MIN_MSG_LEN:
        return {"search": False, "query": "", "reason": "короткое сообщение"}
    if not available():
        return default

    user_block = _build_user_block(history or [], user_msg)
    data = _call_model(_PROMPT, user_block, num_predict=200)

    if data is None:
        print("   🔄 search_agent: retry с минимальным промптом")
        short_system = (
            "Ответь JSON: нужен ли поиск в интернете? "
            "Искать только: новости, погода, курсы, товары, "
            "публичные факты. Не искать: приветствия, эмоции, "
            "про собеседника и диалог. "
            '{"search": bool, "query": "3-10 слов", "reason": "кратко"}'
        )
        data = _call_model(short_system, user_msg[:300], num_predict=150)

    if not isinstance(data, dict):
        return {"search": False, "query": "",
                "reason": "не удалось распарсить после retry"}

    search = bool(data.get("search"))
    query = (data.get("query") or "").strip()
    reason = (data.get("reason") or "").strip()

    if search and not query:
        return {"search": False, "query": "",
                "reason": "search=true без query"}

    if search and _looks_like_self_topic(query):
        return {"search": False, "query": "",
                "reason": f"self-topic: {query[:60]}"}

    if search and _looks_like_garbage(query):
        return {"search": False, "query": "",
                "reason": f"garbage query: {query[:60]}"}

    if search and query:
        query = _enrich_query(query)

    return {"search": search, "query": query, "reason": reason}