# llm.py — обёртка над Ollama. Reasoning включён.
#
# Если модель кладёт thinking в content (бывает у Gemma/DeepSeek) —
# вычищаем его и переносим в thinking.

import base64
import re
import ollama
from config import MODEL, VISION_MODEL, LLM_NUM_CTX
from lada.core.persona import get_persona


# Маркеры, по которым узнаём, что content — это thinking, а не ответ
THINKING_MARKERS = [
    "Оценка состояния:",
    "Оценка собеседника:",
    "Оценка ситуации:",
    "Оценка сообщения:",
    "Решение:",
    "Выбор фото:",
    "Формулировка:",
    "Внимание: Текст",
    "Внимание: Текстовый",
    "thinking:",
    "final answer:",
]


def _looks_like_thinking(text: str) -> bool:
    """
    True, если text похож на thinking, а не на ответ.
    Используем специфичные маркеры + проверку на структуру.
    """
    if not text:
        return False

    low = text.lower()
    for marker in THINKING_MARKERS:
        if marker.lower() in low:
            return True

    # или начинается с "thinking:" или с цифры+точки в первых строках
    first_line = text.strip().split("\n", 1)[0].lower()
    if first_line.startswith("thinking:"):
        return True

    # структура вида "1. ... 2. ... 3. ... 4. ..." + длинный текст
    numbered = re.findall(r'^\s*\d+\.\s', text, flags=re.MULTILINE)
    if len(numbered) >= 3 and len(text) > 400:
        return True

    return False


def _clean_thinking_artifacts(text: str) -> str:
    """Убирает из content явные следы thinking, если они смешаны с ответом."""
    if not text:
        return text

    # отрезаем всё до маркера "Ответ:" или "Текстовый ответ:" если он есть
    for marker in ("Ответ:", "Текстовый ответ:", "Финальный ответ:"):
        if marker in text:
            after = text.split(marker, 1)[1].strip()
            if after:
                return after

    return text.strip()


def ask(messages: list, temperature: float = 0.8, num_predict: int = 700):
    """
    Возвращает (content, thinking).
    Если модель положила thinking в content — переносим.
    """
    full = [{"role": "system", "content": get_persona()}] + messages
    opts = {
        "temperature": temperature,
        "num_predict": num_predict,
        "num_ctx": LLM_NUM_CTX,
    }
    try:
        resp = ollama.chat(model=MODEL, messages=full, think=True, options=opts)
    except TypeError:
        try:
            resp = ollama.chat(model=MODEL, messages=full, options=opts)
        except Exception as e:
            return f"⚠️ Ошибка LLM: {e}", ""
    except Exception as e:
        return f"⚠️ Ошибка LLM: {e}", ""

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()

    # ─── Фикс: если content выглядит как thinking ───
    if _looks_like_thinking(content):
        if not thinking:
            thinking = content
        else:
            thinking = thinking + "\n\n" + content
        content = ""

    # ─── Фикс: если в content смешаны thinking и ответ ───
    if content and not _looks_like_thinking(content):
        cleaned = _clean_thinking_artifacts(content)
        if cleaned != content:
            content = cleaned

    return content, thinking


def ask_text(messages: list) -> str:
    content, _ = ask(messages)
    return content


def ask_reflection(context: str) -> str:
    REFLECTION_PROMPT = (
        "Ты ведёшь личный дневник от лица Лады. "
        "Проанализируй то, что ты знаешь, и напиши краткую рефлексию (3-5 предложений). "
        "Пиши от первого лица, честно, без лишних слов."
    )
    messages = [
        {"role": "system", "content": get_persona() + "\n\n" + REFLECTION_PROMPT},
        {"role": "user", "content": f"Вот мои текущие знания и общение:\n\n{context}"},
    ]
    try:
        resp = ollama.chat(
            model=MODEL, messages=messages, think=True,
            options={"temperature": 0.9, "num_predict": 2000, "num_ctx": LLM_NUM_CTX},
        )
    except TypeError:
        try:
            resp = ollama.chat(
                model=MODEL, messages=messages,
                options={"temperature": 0.9, "num_predict": 2000, "num_ctx": LLM_NUM_CTX},
            )
        except Exception as e:
            return f"⚠️ Ошибка рефлексии: {e}"
    except Exception as e:
        return f"⚠️ Ошибка рефлексии: {e}"

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()

    if _looks_like_thinking(content):
        content = thinking or content

    if not content:
        content = thinking

    return content


def describe_image(image_bytes: bytes, prompt: str = "Что на этом изображении? Опиши кратко.") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    messages = [{"role": "user", "content": prompt, "images": [b64]}]
    try:
        resp = ollama.chat(model=VISION_MODEL, messages=messages)
        return resp["message"]["content"].strip()
    except Exception as e:
        return f"⚠️ Ошибка vision: {e}"