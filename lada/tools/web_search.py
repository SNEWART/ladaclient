# web_search.py — поиск в интернете через DuckDuckGo

from config import WEB_SEARCH_MAX_RESULTS

try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False
    print("⚠️ Библиотека ddgs не установлена. Веб-поиск недоступен.")
    print("   Установи: pip install ddgs")


def search(query: str, max_results: int = None) -> list:
    """Возвращает список результатов: [{"title": ..., "body": ..., "href": ...}, ...]"""
    if not DDGS_AVAILABLE:
        return []
    if max_results is None:
        max_results = WEB_SEARCH_MAX_RESULTS
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return results
    except Exception as e:
        print(f"⚠️ Ошибка веб-поиска: {e}")
        return []


def search_and_format(query: str) -> str:
    """Возвращает отформатированную строку для вставки в контекст LLM."""
    results = search(query)
    if not results:
        return f"Поиск по запросу «{query}» не дал результатов."

    lines = [f"Результаты поиска по запросу «{query}»:"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "—")
        body = r.get("body", "").strip()
        href = r.get("href", "")
        lines.append(f"\n{i}. {title}")
        lines.append(f"   {body[:300]}")
        if href:
            lines.append(f"   Источник: {href}")
    return "\n".join(lines)