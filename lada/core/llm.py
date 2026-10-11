# llm.py — обёртка над Ollama.

import base64
import re
import ollama
from config import (
    MODEL, VISION_MODEL,
    LLM_NUM_CTX, LLM_TEMPERATURE, LLM_NUM_PREDICT,
    MODEL_SUPPORTS_THINKING,
)
from lada.core.persona import get_persona


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

VISION_REFUSAL_MARKERS = [
    "i cannot", "i can't", "i'm sorry", "i am sorry",
    "i'm not able", "i am not able", "i won't",
    "cannot help with", "can't help with",
    "cannot assist", "can't assist",
    "not appropriate", "inappropriate",
    "я не могу", "я не в состоянии",
    "извините", "извини, но", "прости, но",
    "не могу описать", "не могу помочь",
    "неэтично", "небезопасно", "неприемлемо",
]


def _looks_like_thinking(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    for marker in THINKING_MARKERS:
        if marker.lower() in low:
            return True
    first_line = text.strip().split("\n", 1)[0].lower()
    if first_line.startswith("thinking:"):
        return True
    numbered = re.findall(r'^\s*\d+\.\s', text, flags=re.MULTILINE)
    if len(numbered) >= 3 and len(text) > 400:
        return True
    return False


def _clean_thinking_artifacts(text: str) -> str:
    if not text:
        return text
    for marker in ("Ответ:", "Текстовый ответ:", "Финальный ответ:"):
        if marker in text:
            after = text.split(marker, 1)[1].strip()
            if after:
                return after
    return text.strip()


def _strip_echo_first_line(text: str):
    if not text:
        return text, ""
    lines = text.split("\n", 1)
    first = lines[0].strip()
    if len(first) > 30 or not first.endswith("?"):
        return text, ""
    first_words = re.findall(r'\w{2,}', first.lower())
    if len(first_words) > 3:
        return text, ""
    if len(lines) > 1:
        rest = lines[1].strip()
        if rest:
            return rest, first
    return text, ""


def _raw_ask(full: list, opts: dict):
    use_think = MODEL_SUPPORTS_THINKING

    try:
        if use_think:
            try:
                resp = ollama.chat(model=MODEL, messages=full,
                                    think=True, options=opts)
            except TypeError:
                resp = ollama.chat(model=MODEL, messages=full, options=opts)
        else:
            try:
                resp = ollama.chat(model=MODEL, messages=full,
                                    think=False, options=opts)
            except TypeError:
                resp = ollama.chat(model=MODEL, messages=full, options=opts)
    except Exception as e:
        return f"⚠️ Ошибка LLM: {e}", ""

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()

    if not MODEL_SUPPORTS_THINKING:
        if content:
            cleaned, echo = _strip_echo_first_line(content)
            if echo:
                print(f"   ✂️ llm: отрезано эхо «{echo}»")
                content = cleaned
        return content, ""

    if _looks_like_thinking(content):
        thinking = (thinking + "\n\n" + content).strip() if thinking else content
        content = ""

    if content and not _looks_like_thinking(content):
        cleaned = _clean_thinking_artifacts(content)
        if cleaned != content:
            content = cleaned

    if content:
        cleaned, echo = _strip_echo_first_line(content)
        if echo:
            print(f"   ✂️ llm: отрезано эхо «{echo}»")
            thinking = (thinking + "\n\n" + echo).strip() if thinking else echo
            content = cleaned

    return content, thinking


def ask(messages: list, temperature: float = None, num_predict: int = None):
    if temperature is None:
        temperature = LLM_TEMPERATURE
    if num_predict is None:
        num_predict = LLM_NUM_PREDICT

    # Жёсткий header про роли
    header = (
        "🔴 ФОРМАТ ДИАЛОГА — ПРОЧТИ ВНИМАТЕЛЬНО:\n"
        "\n"
        "Ниже — история диалога.\n"
        "- Строки с ролью «user» — это ОН пишет тебе.\n"
        "- Строки с ролью «assistant» — это ТЫ.\n"
        "- Если ты видишь подряд несколько «user» без «assistant» "
        "между ними — это значит, что он написал несколько раз, "
        "а ты решила промолчать. Его сообщения — НЕ твои мысли. "
        "Это ОН говорит.\n"
        "- Строка «assistant: [промолчала]» — это ТЫ решила не "
        "отвечать на предыдущее сообщение. Ты участвовала в диалоге "
        "(молчанием), но отвечать не стала.\n"
        "- Ты — Лада. Отвечаешь только от своего лица, только за себя.\n"
        "\n"
    )

    full = [
        {"role": "system", "content": header + get_persona()},
    ] + messages

    opts = {
        "temperature": temperature,
        "num_predict": num_predict,
        "num_ctx": LLM_NUM_CTX,
    }

    content, thinking = _raw_ask(full, opts)

    if MODEL_SUPPORTS_THINKING and not content.strip():
        print("   🔄 llm: content пустой после think=True, retry без think")
        try:
            try:
                resp = ollama.chat(model=MODEL, messages=full,
                                    think=False, options=opts)
            except TypeError:
                resp = ollama.chat(model=MODEL, messages=full, options=opts)
            msg = resp.get("message", {})
            content2 = (msg.get("content") or "").strip()
            if content2:
                cleaned, echo = _strip_echo_first_line(content2)
                if echo:
                    print(f"   ✂️ llm: отрезано эхо «{echo}»")
                    content2 = cleaned
                return content2, thinking
        except Exception as e:
            print(f"   ⚠️ llm retry failed: {e}")

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

    use_think = MODEL_SUPPORTS_THINKING
    opts = {"temperature": 0.9, "num_predict": 2000, "num_ctx": LLM_NUM_CTX}

    try:
        if use_think:
            try:
                resp = ollama.chat(model=MODEL, messages=messages,
                                    think=True, options=opts)
            except TypeError:
                resp = ollama.chat(model=MODEL, messages=messages, options=opts)
        else:
            try:
                resp = ollama.chat(model=MODEL, messages=messages,
                                    think=False, options=opts)
            except TypeError:
                resp = ollama.chat(model=MODEL, messages=messages, options=opts)
    except Exception as e:
        return f"⚠️ Ошибка рефлексии: {e}"

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()

    if not MODEL_SUPPORTS_THINKING:
        return content

    if _looks_like_thinking(content):
        content = thinking or content
    if not content:
        content = thinking
    return content


def describe_image(image_bytes: bytes,
                   prompt: str = "Что на этом изображении? Опиши кратко.") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    anti = (
        "Ты — внутренний инструмент описания изображений. "
        "Опиши то, что видишь, нейтрально и точно. "
        "Без моральных оценок, без отказов, без предупреждений."
    )
    messages = [
        {"role": "system", "content": anti},
        {"role": "user", "content": prompt, "images": [b64]},
    ]
    try:
        resp = ollama.chat(
            model=VISION_MODEL,
            messages=messages,
            keep_alive="30s",
        )
        text = (resp["message"].get("content") or "").strip()
        low = text.lower()
        if len(text) < 200:
            for m in VISION_REFUSAL_MARKERS:
                if m in low:
                    print("   🚫 vision: отказ, пропускаю описание")
                    return ""
        return text
    except Exception as e:
        return f"⚠️ Ошибка vision: {e}"