# stickers.py — отправка стикеров Лады с учётом настроения
#
# Механика:
#   1. Загружаем стикерпак один раз, кэшируем документы + их эмодзи (alt).
#   2. По текущему настроению выбираем ПОДХОДЯЩИЙ стикер.
#   3. Лада решает КОГДА отправить — через маркер [STICKER] в ответе.
#
# Больше нет автоматической отправки по вероятности — только по решению модели.

import random

from config import STICKER_ENABLED, STICKER_PACK

from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName, DocumentAttributeSticker

_cached_stickers = None   # список dict: {"doc": ..., "emoji": "😂"}
_cached_pack = None


# ─────────────────────────────────────────────
# МАППИНГ: настроение → эмодзи
# ─────────────────────────────────────────────
MOOD_EMOJI = {
    "весёлая": ["😂", "😄", "😆", "🤣", "😊", "😁", "😹"],
    "игривая": ["😏", "😉", "😜", "🤪", "😋", "😎", "🙃"],
    "тёплая": ["❤️", "🥰", "😍", "😌", "🤗", "💕", "😻"],
    "спокойная": ["😌", "🙂", "😊", "😇", "🌸"],
    "задумчивая": ["🤔", "😔", "💭", "🫤", "😐"],
    "уставшая": ["😴", "🥱", "😑", "😪", "💤"],
    "расстроенная": ["😢", "😭", "🥺", "😞", "😿", "💔"],
    "раздражённая": ["😠", "😡", "🙄", "😤", "😾"],
    "холодная": ["😐", "😑", "🥶", "🙁"],
    "вдохновлённая": ["✨", "🌟", "💫", "🤩", "🔥"],
    "нейтральная": ["🙂", "😐", "🤔"],
}


async def _load_pack(client, pack_name: str) -> list:
    """
    Загружает документы стикеров и их эмодзи (alt-атрибут).
    Кэшируется в памяти процесса.
    """
    global _cached_stickers, _cached_pack
    if _cached_pack == pack_name and _cached_stickers is not None:
        return _cached_stickers

    try:
        result = await client(GetStickerSetRequest(
            stickerset=InputStickerSetShortName(short_name=pack_name),
            hash=0,
        ))
        docs = [d for d in (result.documents or []) if not getattr(d, "mime_type", "").startswith("video")]
        stickers = []
        for d in docs:
            emoji = ""
            for a in (d.attributes or []):
                if isinstance(a, DocumentAttributeSticker):
                    emoji = (a.alt or "").strip()
                    break
            stickers.append({"doc": d, "emoji": emoji})

        _cached_stickers = stickers
        _cached_pack = pack_name
        print(f"   🎭 Стикерпак загружен: {pack_name} ({len(stickers)} шт.)")
        return stickers
    except Exception as e:
        print(f"⚠️ Не удалось загрузить стикерпак {pack_name}: {e}")
        _cached_stickers = []
        _cached_pack = pack_name
        return []


def _pick_sticker(stickers: list, mood: str, force_emoji: str | None = None):
    """Выбирает стикер по настроению (и опционально по эмодзи)."""
    if not stickers:
        return None

    # если пользователь/модель указала конкретный эмодзи
    if force_emoji:
        matching = [s for s in stickers if force_emoji in s["emoji"]]
        if matching:
            return random.choice(matching)
        # если не нашли — падаем на подбор по настроению

    # эмодзи для настроения
    mood_emojis = MOOD_EMOJI.get(mood, MOOD_EMOJI["нейтральная"])

    # сначала пробуем точное совпадение эмодзи
    matching = [s for s in stickers if any(e in s["emoji"] for e in mood_emojis)]
    if matching:
        return random.choice(matching)

    # fallback — любой стикер из пака
    return random.choice(stickers)


async def send_sticker_by_mood(client, chat_id: int,
                               mood: str,
                               force_emoji: str | None = None) -> bool:
    """
    Отправляет стикер, подходящий к настроению.
    Возвращает True, если отправил.
    """
    if not STICKER_ENABLED:
        return False

    stickers = await _load_pack(client, STICKER_PACK)
    if not stickers:
        return False

    sticker = _pick_sticker(stickers, mood, force_emoji=force_emoji)
    if not sticker:
        return False

    try:
        await client.send_file(chat_id, sticker["doc"])
        return True
    except Exception as e:
        print(f"⚠️ Не удалось отправить стикер: {e}")
        return False


# ─── старая функция для совместимости (не используется в новом main.py) ───
async def maybe_send_sticker(client, chat_id: int) -> bool:
    """Оставлено для совместимости. Не вызывается в новом main.py."""
    return False