# weather.py — погода через DuckDuckGo (использует web_search.py)
#
# Не делает HTTP напрямую — дёргает web_search.search()
# и вытаскивает погоду из сниппетов.
#
# Запрос: "погода <город> сегодня"
# Парсим первый сниппет, ищем температуру и описание.

import re
from lada.tools import web_search

# кэш на 30 минут
_cache: dict = {}
CACHE_TTL_SECONDS = 30 * 60


# Слова-маркеры для определения погоды в тексте
WEATHER_KEYWORDS = [
    "°C", "°c", "градус", "температура", "ясно", "облачно", "пасмурно",
    "дожд", "снег", "ветер", "влажност", "солнечно", "гроз", "туман",
    "weather", "temperature", "cloudy", "sunny", "rain", "snow",
]


def _extract_weather_from_text(text: str) -> str | None:
    """
    Пытается вытащить короткое описание погоды из длинного текста.
    Ищет предложение с температурой или погодным маркером.
    """
    if not text:
        return None

    # Ищем температуру
    temp_match = re.search(r'[-+]?\d+\s*°\s*[Cc]', text)
    # Ищем описание погоды
    has_keyword = any(kw.lower() in text.lower() for kw in WEATHER_KEYWORDS)

    if not temp_match and not has_keyword:
        return None

    # Разбиваем на предложения, берём то, где есть температура
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if temp_match:
        for s in sentences:
            if re.search(r'[-+]?\d+\s*°\s*[Cc]', s):
                return s.strip()[:200]

    # Fallback: первое предложение с ключевым словом
    for s in sentences:
        if any(kw.lower() in s.lower() for kw in WEATHER_KEYWORDS):
            return s.strip()[:200]

    return None


def get_weather(city: str, force: bool = False) -> str | None:
    """
    Возвращает короткое описание погоды или None.
    Использует существующий web_search (DDG).
    """
    if not city or not city.strip():
        return None

    city = city.strip()
    key = city.lower()

    import time
    now = time.time()

    if not force and key in _cache:
        ts, val = _cache[key]
        if now - ts < CACHE_TTL_SECONDS:
            return val

    # Запрос по-русски, DDG хорошо отвечает на такие
    query = f"погода {city} сегодня"

    try:
        results = web_search.search(query, max_results=3)
    except Exception as e:
        print(f"   ⚠️ weather: ошибка поиска: {e}")
        return None

    if not results:
        return None

    # Ищем в первых результатах упоминание погоды
    for r in results:
        title = r.get("title", "")
        body = r.get("body", "")
        combined = f"{title}. {body}"

        extracted = _extract_weather_from_text(combined)
        if extracted:
            weather_str = f"{city}: {extracted}"
            _cache[key] = (now, weather_str)
            return weather_str

    return None