# splitter.py — вероятностная разбивка ответа на несколько сообщений
#
# FIX: склейка коротких первых частей. "А ты?" (8 симв) раньше летел
# отдельным пузырём и выглядел как бред. Теперь части короче 25 симв
# приклеиваются к следующей.

import asyncio
import random
import re

from config import (
    SPLIT_PROBABILITY, SPLIT_MAX_PARTS, SPLIT_MIN_LEN,
    SPLIT_TYPING_SPEED, SPLIT_MIN_DELAY, SPLIT_MAX_DELAY,
    SPLIT_BETWEEN_DELAY,
)

MAX_PART_LEN = 220
MIN_FIRST_PART_LEN = 25


def _split_by_sentences(text: str) -> list:
    """Разбивает по предложениям. Действия в звёздочках не ломает."""
    protected = []

    def protect(m):
        protected.append(m.group(0))
        return f"\x00{len(protected)-1}\x00"

    text_safe = re.sub(r'\*[^*\n]+\*', protect, text)

    parts = re.split(r'(?<=[.!?…])\s+|\n{2,}', text_safe)

    restored = []
    for p in parts:
        p = re.sub(r'\x00(\d+)\x00', lambda m: protected[int(m.group(1))], p)
        p = p.strip()
        if p:
            restored.append(p)
    return restored


def _hard_wrap(text: str, max_len: int = MAX_PART_LEN) -> list:
    """Принудительно режет длинный текст на части <= max_len."""
    if len(text) <= max_len:
        return [text]

    protected = []

    def protect(m):
        protected.append(m.group(0))
        return f"\x00{len(protected)-1}\x00"

    safe = re.sub(r'\*[^*\n]+\*', protect, text)

    chunks = []
    pieces = re.split(r'(?<=[,;—:])\s+', safe)

    current = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= max_len:
            current += " " + piece
        else:
            chunks.append(current)
            current = piece

        while len(current) > max_len:
            space_idx = current.rfind(" ", 0, max_len)
            if space_idx <= 0:
                space_idx = max_len
            chunks.append(current[:space_idx].rstrip())
            current = current[space_idx:].lstrip()

    if current:
        chunks.append(current)

    restored = []
    for c in chunks:
        c = re.sub(r'\x00(\d+)\x00', lambda m: protected[int(m.group(1))], c)
        c = c.strip()
        if c:
            restored.append(c)
    return restored


def split_text(text: str,
               max_parts: int = None,
               min_len: int = None) -> list:
    """
    Разбивает текст на части по предложениям.
    Дополнительно режет слишком длинные части.
    FIX: короткие первые части (< MIN_FIRST_PART_LEN) склеиваются
    со следующими — не отделяются в самостоятельный пузырь.
    """
    max_parts = max_parts or SPLIT_MAX_PARTS
    min_len = min_len or SPLIT_MIN_LEN

    text = text.strip()
    if not text:
        return []
    if len(text) < min_len:
        return [text]

    parts = _split_by_sentences(text)

    # FIX: склейка коротких частей в начале
    merged = []
    buffer = ""
    for p in parts:
        if not buffer:
            buffer = p
            continue
        if len(buffer) < MIN_FIRST_PART_LEN:
            buffer = buffer + " " + p
        else:
            merged.append(buffer)
            buffer = p
    if buffer:
        merged.append(buffer)
    parts = merged

    if len(parts) <= 1:
        return _hard_wrap(text)

    expanded = []
    for p in parts:
        if len(p) > MAX_PART_LEN:
            expanded.extend(_hard_wrap(p))
        else:
            expanded.append(p)
    parts = expanded

    if len(parts) <= 1:
        return [text]

    if len(parts) <= max_parts:
        return parts

    n = len(parts)
    groups = [[] for _ in range(max_parts)]
    for i, p in enumerate(parts):
        idx = min(i * max_parts // n, max_parts - 1)
        groups[idx].append(p)

    result = []
    for g in groups:
        if not g:
            continue
        chunk = " ".join(g)
        if len(chunk) > MAX_PART_LEN:
            result.extend(_hard_wrap(chunk))
        else:
            result.append(chunk)
    return result


def _typing_delay(text: str) -> float:
    delay = len(text) / max(SPLIT_TYPING_SPEED, 1)
    return max(SPLIT_MIN_DELAY, min(SPLIT_MAX_DELAY, delay))


async def send_in_parts(client, chat_id: int, text: str) -> list:
    """
    Отправляет текст одним или несколькими сообщениями.
    Решение о разбивке — вероятностное.
    """
    if len(text) >= SPLIT_MIN_LEN:
        has_long_block = len(text) > MAX_PART_LEN * 2

        if has_long_block or random.random() < SPLIT_PROBABILITY:
            parts = split_text(text)
            if len(parts) <= 1:
                parts = [text]
        else:
            parts = [text]
    else:
        parts = [text]

    sent = []
    for i, part in enumerate(parts):
        if i > 0:
            try:
                async with client.action(chat_id, "typing"):
                    await asyncio.sleep(_typing_delay(part))
            except Exception:
                await asyncio.sleep(_typing_delay(part))

        try:
            msg = await client.send_message(chat_id, part)
            sent.append(msg)
        except Exception as e:
            print(f"⚠️ splitter: не удалось отправить часть: {e}")
            return sent

        if i < len(parts) - 1:
            await asyncio.sleep(SPLIT_BETWEEN_DELAY)

    return sent