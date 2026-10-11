# search_agent.py — решает, нужен ли поиск, и формирует КОНКРЕТНЫЙ запрос.
#
# FIX: используем нативный JSON-режим Ollama (format="json").
# Модель физически не может ответить не-JSON. Это убирает 90%
# случаев "не удалось распарсить JSON".
#
# FIX: при провале логируем сырой ответ модели (первые 200 симв.)
# — чтобы понимать, что она генерирует, если JSON всё равно не
# получился.

import json
import re
from datetime import datetime

import ollama

from config import (
    SEARCH_AGENT_ENABLED, SEARCH_AGENT_MODEL,
    SEARCH_AGENT_MIN_MSG_LEN,
)


_PROMPT = (
    "Ты — вспомогательный агент-планировщик поиска. "
    "Реши, нужен ли поиск в интернете, чтобы ответить на сообщение, "
    "и сформулируй конкретный запрос.\n\n"
    "🔴 ИСКАТЬ ТОЛЬКО ВНЕШНИЕ ФАКТЫ. Интернет не знает ничего про "
    "Ладу, про пользователя и про их диалог.\n\n"
    "НЕ ИСКАТЬ:\n"
    "- про саму Ладу, про её жизнь, настроение, дела\n"
    "- про пользователя личное (работа, здоровье, семья, эмоции)\n"
    "- про диалог («что мы обсуждали», «что он говорил»)\n"
    "- приветствия, «как дела», «что нового у тебя»\n"
    "- философию, мнения, чувства\n\n"
    "ИСКАТЬ:\n"
    "- новости, погода, курс валют, биржа, цены\n"
    "- характеристики товаров, даты выхода, цены продуктов\n"
    "- публичные факты о знаменитостях, компаниях, странах\n"
    "- явная просьба: «погугли», «найди», «поищи»\n\n"
    "Если тема ссылается на контекст («этот ноут», «про него», "
    "«подробнее») — включи тему из контекста в запрос.\n\n"
    "ЗАПРОС должен быть конкретным: модель, год, место.\n"
    "Не «новости США», а «новости США выборы октябрь 2026».\n"
    "Не «характеристики ноута», а «MacBook Neo 2026 характеристики».\n\n"
    "Верни СТРОГО JSON:\n"
    "{\n"
    '  "search": true или false,\n'
    '  "query": "конкретный поисковый запрос 3-12 слов или пустая строка",\n'
    '  "reason": "кратко почему"\n'
    "}"
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
    """Парсит JSON из ответа. Работает и с чистым JSON, и с обёрнутым."""
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
        lines.append("КОНТЕКСТ (последние сообщения):")
        for m in history[-6:]:
            role = "Пользователь" if m.get("role") == "user" else "Лада"
            content = (m.get("content") or "").strip()[:300]
            if content:
                lines.append(f"  {role}: {content}")
        lines.append("")

    lines.append("ТЕКУЩЕЕ СООБЩЕНИЕ:")
    lines.append(f"  {current_msg[:600]}")
    lines.append("")
    lines.append("Реши, нужен ли поиск. Если про Ладу, про пользователя, "
                 "про диалог или про эмоции — search=false.")
    return "\n".join(lines)


def _looks_like_self_topic(query: str) -> bool:
    """Программный фильтр: если в query упоминается Лада/диалог — не искать."""
    low = query.lower()
    bad_markers = [
        "лада", "лады", "ладе", "ладу", "ладо",
        "пользователь", "собеседник",
        "диалог", "разговор", "мы обсуждали",
        "как прошла", "как спалось", "как дела у",
    ]
    return any(m in low for m in bad_markers)


def _call_model(system: str, user: str, num_predict: int = 300):
    """
    Один вызов ollama с JSON-режимом.
    Возвращает dict или None.
    """
    try:
        try:
            # Нативный JSON-режим — есть в свежих версиях ollama
            resp = ollama.chat(
                model=SEARCH_AGENT_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                format="json",
                think=False,
                keep_alive="30s",
                options={"temperature": 0.1, "num_predict": num_predict},
            )
        except TypeError:
            # Старая версия ollama без format — без JSON-режима
            resp = ollama.chat(
                model=SEARCH_AGENT_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                think=False,
                keep_alive="30s",
                options={"temperature": 0.1, "num_predict": num_predict},
            )
    except Exception as e:
        print(f"   ⚠️ search_agent: {e}")
        return None

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()

    data = _extract_json(content)
    if isinstance(data, dict):
        return data

    # Логируем сырой ответ при провале — для диагностики
    print(f"   ⚠️ search_agent: не удалось распарсить. "
          f"content={content[:200]!r}")
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

    # Первая попытка: полный промпт
    data = _call_model(_PROMPT, user_block, num_predict=300)

    # Retry: минимальный промпт, только текущее сообщение
    if data is None:
        print("   🔄 search_agent: retry с минимальным промптом")
        short_prompt = (
            "Реши, нужен ли поиск в интернете. "
            "ИСКАТЬ ТОЛЬКО: новости, погода, курс валют, характеристики "
            "товаров, публичные факты. "
            "НЕ ИСКАТЬ: про Ладу, про пользователя, про диалог, эмоции, "
            "приветствия, «как дела». "
            "JSON: {\"search\": bool, \"query\": \"запрос\", \"reason\": \"почему\"}"
        )
        short_block = f"Сообщение: {user_msg[:400]}"
        data = _call_model(short_prompt, short_block, num_predict=200)

    if not isinstance(data, dict):
        return {"search": False, "query": "",
                "reason": "не удалось распарсить JSON после retry"}

    search = bool(data.get("search"))
    query = (data.get("query") or "").strip()
    reason = (data.get("reason") or "").strip()

    if search and not query:
        search = False
        reason = "search=true без query"

    if search and _looks_like_self_topic(query):
        search = False
        reason = f"self-topic отсечён: {query[:60]}"
        query = ""

    if search and query:
        query = _enrich_query(query)

    return {"search": search, "query": query, "reason": reason}