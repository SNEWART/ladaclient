# lada/brain/obs_validator.py — LLM-валидатор наблюдений.
#
# Работает как ВТОРОЙ слой после регекс-фильтра в combined_eval.
# Регекс ловит форму («Пользователь сказал…», «мус[яюией]»),
# LLM ловит смысл (пересказ реплики vs постоянный факт).
#
# Модель — маленькая (1.5B), задача узкая: valid/trash.
# Если модель недоступна — возвращает "unknown", combined_eval
# тогда доверяет регексу.

import json
import re

import ollama

from config import (
    OBS_VALIDATOR_ENABLED,
    OBS_VALIDATOR_MODEL,
)

_available_cache = None


_SYSTEM = (
    "Ты — валидатор памяти. Реши, можно ли сохранить строку "
    "как ПОСТОЯННЫЙ факт о человеке, или это мусор.\n"
    "\n"
    "ПОСТОЯННЫЙ ФАКТ — то, что остаётся правдой через неделю: "
    "работа, город, хобби, вкусы, характер, семья, питомцы, "
    "привычки, образование, убеждения.\n"
    "\n"
    "НЕ ФАКТ (мусор):\n"
    "— пересказ реплик («он сказал X», «он спросил Y», «он написал»)\n"
    "— сиюминутное состояние («он устал», «ему весело», «он рад»)\n"
    "— всё про Ладу («он заботится о Ладе», «он считает Ладу X»)\n"
    "— атрибуты Лады, приписанные пользователю («у него есть кошка "
    "Муся» — Муся кошка Лады, не его)\n"
    "— общие рассуждения («люди любят…», «пользователи делают…»)\n"
    "— короткие бессмысленные обрывки\n"
    "\n"
    "ПРИМЕРЫ:\n"
    "  «Работает программистом» → valid\n"
    "  «Живёт в Санкт-Петербурге» → valid\n"
    "  «У него есть кот Барсик» → valid\n"
    "  «Учится в РГТУ» → valid\n"
    "  «Любит горы и походы» → valid\n"
    "  «Предпочитает кофе без сахара» → valid\n"
    "  «У него есть кошка по имени Муся» → trash\n"
    "  «Пользователь сказал, что устал» → trash\n"
    "  «Пользователь спросил про работу» → trash\n"
    "  «Он проявляет интерес к Ладе» → trash\n"
    "  «Он пожелал Ладе спокойной ночи» → trash\n"
    "  «Пользователи демонстрируют двойственность» → trash\n"
    "  «Ему грустно» → trash\n"
    "\n"
    "Верни СТРОГО JSON без markdown:\n"
    '{"valid": true или false, "reason": "коротко почему"}'
)


def available() -> bool:
    global _available_cache
    if not OBS_VALIDATOR_ENABLED:
        return False
    if _available_cache is not None:
        return _available_cache
    try:
        ollama.show(OBS_VALIDATOR_MODEL)
        _available_cache = True
    except Exception as e:
        _available_cache = False
        print(f"   ⚠️ obs_validator: модель '{OBS_VALIDATOR_MODEL}' "
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


def validate(text: str) -> tuple:
    """
    Возвращает (verdict, reason):
      verdict = "valid" | "trash" | "unknown"
    unknown — модель не ответила/недоступна/не распарсилось.
    В этом случае combined_eval доверяет регексу.
    """
    if not OBS_VALIDATOR_ENABLED:
        return "unknown", "validator disabled"
    if not text or len(text.strip()) < 5:
        return "trash", "пусто"
    if not available():
        return "unknown", "model unavailable"

    try:
        resp = ollama.chat(
            model=OBS_VALIDATOR_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": text.strip()[:300]},
            ],
            format="json",
            think=False,
            keep_alive="5m",
            options={"temperature": 0.0, "num_predict": 80},
        )
    except TypeError:
        try:
            resp = ollama.chat(
                model=OBS_VALIDATOR_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": text.strip()[:300]},
                ],
                think=False,
                keep_alive="5m",
                options={"temperature": 0.0, "num_predict": 80},
            )
        except Exception as e:
            return "unknown", f"ошибка: {e}"
    except Exception as e:
        return "unknown", f"ошибка: {e}"

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    if not content:
        thinking = (msg.get("thinking") or "").strip()
        if thinking:
            content = thinking

    data = _extract_json(content)
    if not isinstance(data, dict):
        return "unknown", f"не распарсилось: {content[:80]!r}"

    valid = data.get("valid")
    reason = (data.get("reason") or "").strip()

    if isinstance(valid, bool):
        return ("valid" if valid else "trash"), reason

    # Строковые значения true/false тоже принимаем
    if isinstance(valid, str):
        low = valid.strip().lower()
        if low in ("true", "yes", "valid", "да"):
            return "valid", reason
        if low in ("false", "no", "trash", "нет"):
            return "trash", reason

    return "unknown", f"странный ответ: {valid!r}"