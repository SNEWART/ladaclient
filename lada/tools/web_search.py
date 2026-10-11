# web_search.py — поиск через DuckDuckGo + фильтр релевантности.
#
# - timeout 12 сек, чтобы не висло на Yahoo
# - fallback по бэкендам: duckduckgo → brave → google
# - маркер [SEARCH_FAILED] при полном провале — handlers это обрабатывает
# - фильтр релевантности по стеммингу

import re
from datetime import datetime

from config import WEB_SEARCH_MAX_RESULTS

try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False
    print("⚠️ Библиотека ddgs не установлена. Веб-поиск недоступен.")
    print("   Установи: pip install ddgs")


STOP_WORDS = {
    "и", "в", "на", "с", "по", "для", "из", "к", "о", "об", "от",
    "до", "за", "не", "но", "а", "или", "что", "как", "это", "у",
    "же", "бы", "ли", "он", "она", "они", "мы", "вы", "я", "ты",
    "его", "её", "ее", "их", "нас", "вас", "мне", "тебе", "нам",
    "было", "были", "быть", "есть", "был", "была",
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to",
    "for", "with", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "he", "she", "they",
    "we", "you", "i", "my", "his", "her", "its", "our", "your",
}

MIN_WORD_LEN = 4
MIN_COVERAGE = 0.5
HTTP_TIMEOUT = 12


def _stem(word: str) -> str:
    w = word.lower().strip()
    if len(w) <= 5:
        return w
    return w[:5]


def _significant_words(text: str) -> set:
    if not text:
        return set()
    words = re.findall(r'[а-яёa-z0-9]{2,}', text.lower())
    out = set()
    for w in words:
        if w in STOP_WORDS:
            continue
        if len(w) < MIN_WORD_LEN:
            continue
        out.add(_stem(w))
    return out


def _coverage(query_words: set, text: str) -> float:
    if not query_words:
        return 0.0
    text_words = _significant_words(text)
    if not text_words:
        return 0.0
    hits = 0
    for qw in query_words:
        if any(tw.startswith(qw) for tw in text_words):
            hits += 1
    return hits / len(query_words)


def _do_search(query: str, max_results: int) -> list:
    """
    Пробует несколько бэкендов DDG. Возвращает первый непустой результат.
    """
    if not DDGS_AVAILABLE:
        return []

    backends = ["duckduckgo", "brave", "google"]

    for be in backends:
        try:
            with DDGS(timeout=HTTP_TIMEOUT) as ddgs:
                try:
                    results = list(ddgs.text(
                        query, max_results=max_results, backend=be
                    ))
                except TypeError:
                    # старая версия ddgs без backend
                    results = list(ddgs.text(
                        query, max_results=max_results
                    ))
            if results:
                return results
        except Exception as e:
            print(f"   ⚠️ ddgs backend '{be}': {e}")
            continue

    return []


def search(query: str, max_results: int = None) -> list:
    if max_results is None:
        max_results = WEB_SEARCH_MAX_RESULTS
    return _do_search(query, max_results)


def search_and_format(query: str) -> str:
    results = search(query)

    today = datetime.now().strftime("%d.%m.%Y")
    header = (
        f"=== РЕЗУЛЬТАТЫ ПОИСКА В ИНТЕРНЕТЕ ===\n"
        f"Запрос: «{query}»\n"
        f"Сегодня: {today}\n"
    )

    if not results:
        return (
            header
            + "\n[SEARCH_FAILED]\n\n"
            "Поиск НЕ ДАЛ результатов (таймаут или пустой ответ). "
            "Ничего не выдумывай. Скажи честно: «ничего конкретного не нашла»."
        )

    # ─── Фильтр релевантности ───
    query_words = _significant_words(query)
    kept = []
    dropped = 0

    for r in results:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        cov = _coverage(query_words, f"{title}. {body}")
        if cov >= MIN_COVERAGE:
            kept.append({**r, "_coverage": cov})
        else:
            dropped += 1

    if not kept and results:
        scored = []
        for r in results:
            title = (r.get("title") or "").strip()
            body = (r.get("body") or "").strip()
            cov = _coverage(query_words, f"{title}. {body}")
            scored.append({**r, "_coverage": cov})
        scored.sort(key=lambda x: x["_coverage"], reverse=True)
        if scored[0]["_coverage"] >= 0.2:
            kept = [scored[0]]

    if dropped:
        print(f"   🔍 web_search: отфильтровано {dropped}/{len(results)} "
              f"нерелевантных")

    if not kept:
        return (
            header
            + "\n[SEARCH_FAILED]\n\n"
            f"Найдено {len(results)} результатов, но ни один не относится "
            "к теме напрямую. Скажи честно: «по этой теме ничего "
            "конкретного не нашла». Не выдумывай."
        )

    lines = [header]
    for i, r in enumerate(kept, 1):
        title = (r.get("title") or "—").strip()
        body = (r.get("body") or "").strip()
        href = (r.get("href") or "").strip()
        cov = r.get("_coverage", 0.0)
        lines.append(f"\n{i}. {title}")
        if body:
            lines.append(f"   {body[:400]}")
        if href:
            lines.append(f"   Источник: {href}")
        lines.append(f"   (релевантность: {cov:.0%})")

    lines.append("")
    lines.append(
        "⚠️ КАК ИСПОЛЬЗОВАТЬ ЭТИ ДАННЫЕ:\n"
        "- Опирайся ТОЛЬКО на то, что написано выше. Не выдумывай.\n"
        "- Отвечай КОНКРЕТНО: 2-3 факта из результатов (что случилось, "
        "где, когда). Без «какие-то события», «что-то происходит».\n"
        "- Если в результатах нет конкретики по вопросу — честно скажи "
        "«ничего конкретного не нашла».\n"
        "- Не философствуй. Не обобщай.\n"
        "- Можешь добавить один комментарий от себя (в характере Лады), "
        "но после фактов, не вместо них."
    )

    return "\n".join(lines)