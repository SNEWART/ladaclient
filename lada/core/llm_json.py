# llm_json.py — единая обёртка для JSON-запросов к Ollama

import asyncio
import json
import re
import ollama
from config import MODEL


def _extract_json(text: str):
    """Ищет JSON-объект в тексте, даже если вокруг мусор."""
    if not text:
        return None

    for marker in ["...done thinking.", "done thinking.", "</think>", "Here's a thinking process"]:
        if marker in text:
            parts = text.split(marker, 1)
            if len(parts) > 1 and parts[1].strip():
                text = parts[1]
            break

    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    for pattern in (r'\{.*\}', r'\[.*\]'):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue

    return None


def chat_json_sync(messages: list, prompt_name: str = "json"):
    try:
        resp = ollama.chat(
            model=MODEL,
            messages=messages,
            think=False,
            options={"temperature": 0.3, "num_predict": 500},
        )
    except TypeError:
        try:
            resp = ollama.chat(
                model=MODEL,
                messages=messages,
                options={"temperature": 0.3, "num_predict": 500},
            )
        except Exception as e:
            print(f"⚠️ [{prompt_name}] LLM-ошибка: {e}")
            return None
    except Exception as e:
        print(f"⚠️ [{prompt_name}] LLM-ошибка: {e}")
        return None

    msg = resp.get("message", {})
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()

    data = _extract_json(content)
    if data is not None:
        return data

    if thinking:
        data = _extract_json(thinking)
        if data is not None:
            print(f"   ♻️ [{prompt_name}] JSON взят из thinking")
            return data

    print(f"   ⚠️ [{prompt_name}] не удалось распарсить JSON. content={content[:120]!r}")
    return None


async def chat_json(messages: list, prompt_name: str = "json"):
    return await asyncio.to_thread(chat_json_sync, messages, prompt_name)