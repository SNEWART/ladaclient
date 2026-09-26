# llm_chat.py — обёртка ollama.chat с think=False по умолчанию

import ollama


def chat(model: str, messages: list, **kwargs):
    try:
        return ollama.chat(model=model, messages=messages, think=False, **kwargs)
    except TypeError:
        return ollama.chat(model=model, messages=messages, **kwargs)