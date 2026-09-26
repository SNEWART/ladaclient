# splitter.py — вероятностная разбивка ответа на несколько сообщений
#
# Улучшения:
#   - Не режет *действия в звёздочках* посередине.
#   - Если часть всё равно длинная (> MAX_PART_LEN), режет по запятым,
#     потом по пробелам.
#   - Действия старается оставлять рядом с текстом, к которому привязаны.

import asyncio
import random
import re

from config import (
    SPLIT_PROBABILITY, SPLIT_MAX_PARTS, SPLIT_MIN_LEN,
    SPLIT_TYPING_SPEED, SPLIT_MIN_DELAY, SPLIT_MAX_DELAY,
    SPLIT_BETWEEN_DELAY,
)

# Максимум символов в одном сообщении. Если больше — режем принудительно.
MAX_PART_LEN = 220


def _split_by_sentences(text: str) -> list[str]:
    """Разбивает по предложениям. Действия в звёздочках не ломает."""
    # Защищаем *действия* — заменяем на маркер, чтобы re.split не тронул
    protected = []
    def protect(m):
        protected.append(m.group(0))
        return f"\x00{len(protected)-1}\x00"

    text_safe = re.sub(r'\*[^*\n]+\*', protect, text)

    # Режем по . ! ? … и пустым строкам
    parts = re.split(r'(?<=[.!?…])\s+|\n{2,}', text_safe)

    # Восстанавливаем *действия*
    restored = []
    for p in parts:
        p = re.sub(r'\x00(\d+)\x00', lambda m: protected[int(m.group(1))], p)
        p = p.strip()
        if p:
            restored.append(p)
    return restored


def _hard_wrap(text: str, max_len: int = MAX_PART_LEN) -> list[str]:
    """
    Принудительно режет длинный текст на части <= max_len.
    Сначала по запятым, потом по пробелам. Действия не рвёт.
    """
    if len(text) <= max_len:
        return [text]

    # Защищаем *действия*
    protected = []
    def protect(m):
        protected.append(m.group(0))
        return f"\x00{len(protected)-1}\x00"

    safe = re.sub(r'\*[^*\n]+\*', protect, text)

    chunks = []
    # Сначала пробуем резать по запятым, точкам с запятой, тире
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

        # Если отдельный кусок сам больше max_len — режем по пробелам
        while len(current) > max_len:
            space_idx = current.rfind(" ", 0, max_len)
            if space_idx <= 0:
                space_idx = max_len
            chunks.append(current[:space_idx].rstrip())
            current = current[space_idx:].lstrip()

    if current:
        chunks.append(current)

    # Восстанавливаем *действия*
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
    """
    max_parts = max_parts or SPLIT_MAX_PARTS
    min_len = min_len or SPLIT_MIN_LEN

    text = text.strip()
    if not text:
        return []
    if len(text) < min_len:
        return [text]

    # Шаг 1: разбиваем по предложениям
    parts = _split_by_sentences(text)

    if len(parts) <= 1:
        # Не получилось разбить по предложениям — режем принудительно
        return _hard_wrap(text)

    # Шаг 2: каждую длинную часть дорезаем
    expanded = []
    for p in parts:
        if len(p) > MAX_PART_LEN:
            expanded.extend(_hard_wrap(p))
        else:
            expanded.append(p)
    parts = expanded

    if len(parts) <= 1:
        return [text]

    # Шаг 3: если частей больше max_parts — группируем
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
        # если получившийся блок всё равно длинный — дорезаем
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
    # Длинный текст режем всегда, короткий — как раньше
    if len(text) >= SPLIT_MIN_LEN:
        # если есть длинный блок с действиями — режем принудительно,
        # даже если обычно не делим
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