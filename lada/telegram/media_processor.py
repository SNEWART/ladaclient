# media_processor.py — обработка медиа из Telegram

import tempfile
from pathlib import Path

from telethon.tl import types
from lada.core.llm import describe_image
from config import WHISPER_MODEL


# ─────────────── Whisper (ленивая загрузка) ───────────────

_whisper = None


def _get_whisper():
    global _whisper
    if _whisper is None:
        try:
            from faster_whisper import WhisperModel
            print(f"🎧 Загружаю Whisper: {WHISPER_MODEL}")
            _whisper = WhisperModel(
                WHISPER_MODEL, device="cpu", compute_type="int8"
            )
        except Exception as e:
            print(f"⚠️ Не удалось загрузить Whisper: {e}")
            _whisper = False
    return _whisper


def _transcribe_audio(audio_bytes: bytes) -> str:
    """Транскрибирует аудио через Whisper."""
    whisper = _get_whisper()
    if not whisper:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        segments, info = whisper.transcribe(path, language="ru", beam_size=3)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text
    except Exception as e:
        print(f"⚠️ Ошибка транскрипции: {e}")
        return ""
    finally:
        Path(path).unlink(missing_ok=True)


# ─────────────── Видео (первый кадр) ───────────────

def _extract_first_frame(video_bytes: bytes) -> bytes | None:
    """Извлекает первый кадр из видео и возвращает PNG-байты."""
    try:
        import cv2
    except ImportError:
        print("⚠️ opencv-python-headless не установлен — видео/GIF не обрабатываются")
        return None

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video_bytes)
        path = f.name

    try:
        cap = cv2.VideoCapture(path)
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            return None
        ok, buf = cv2.imencode(".png", frame)
        if not ok:
            return None
        return buf.tobytes()
    except Exception as e:
        print(f"⚠️ Ошибка извлечения кадра: {e}")
        return None
    finally:
        Path(path).unlink(missing_ok=True)


# ─────────────── Основной обработчик ───────────────

async def process_media(message) -> str:
    """Возвращает текстовое описание медиа (или пустую строку)."""
    if not message.media:
        return ""

    # ─────────── ФОТО ───────────
    if isinstance(message.media, types.MessageMediaPhoto):
        try:
            data = await message.download_media(bytes)
            if not data:
                return ""
            desc = describe_image(data)
            return f"[Изображение] {desc}"
        except Exception as e:
            return f"[Изображение: ошибка — {e}]"

    # ─────────── ДОКУМЕНТЫ ───────────
    if isinstance(message.media, types.MessageMediaDocument):
        doc = message.media.document
        if not doc:
            return ""
        mime = doc.mime_type or ""

        # имя файла — в атрибутах, не в doc
        file_name = ""
        for a in doc.attributes:
            if isinstance(a, types.DocumentAttributeFilename):
                file_name = a.file_name or ""
                break

        # проверяем атрибуты
        is_sticker = any(isinstance(a, types.DocumentAttributeSticker) for a in doc.attributes)
        is_animated = any(isinstance(a, types.DocumentAttributeAnimated) for a in doc.attributes)

        # emoji-подсказка для стикеров
        sticker_emoji = ""
        if is_sticker:
            for a in doc.attributes:
                if isinstance(a, types.DocumentAttributeSticker):
                    sticker_emoji = (a.alt or "").strip()
                    break

        # ─── Lottie-стикеры (.tgs) ───
        if mime == "application/x-tgsticker":
            prefix = f"[Анимированный стикер {sticker_emoji}]" if sticker_emoji else "[Анимированный стикер]"
            return prefix

        # ─── Обычные стикеры (webp, png) ───
        if is_sticker and mime in ("image/webp", "image/png"):
            try:
                data = await message.download_media(bytes)
                if not data:
                    return ""
                desc = describe_image(data, prompt="Что изображено на этом стикере? Опиши кратко.")
                prefix = f"[Стикер {sticker_emoji}]" if sticker_emoji else "[Стикер]"
                return f"{prefix} {desc}"
            except Exception as e:
                return f"[Стикер {sticker_emoji}: ошибка — {e}]"

        # ─── GIF / видео ───
        if mime == "video/mp4" or file_name.lower().endswith((".gif", ".mp4")):
            try:
                data = await message.download_media(bytes)
                if not data:
                    return ""
                frame = _extract_first_frame(data)
                if not frame:
                    return "[Видео/анимация: не удалось извлечь кадр]"
                desc = describe_image(frame, prompt="Опиши кратко, что происходит на этом изображении.")
                prefix = "[GIF]" if is_animated else "[Видео]"
                return f"{prefix} {desc}"
            except Exception as e:
                return f"[Видео/анимация: ошибка — {e}]"

        # ─── Голосовые / аудио ───
        if mime.startswith("audio/"):
            try:
                data = await message.download_media(bytes)
                if not data:
                    return ""
                text = _transcribe_audio(data)
                if text:
                    return f"[Голосовое] {text}"
                return "[Голосовое: не удалось распознать]"
            except Exception as e:
                return f"[Голосовое: ошибка — {e}]"

        # ─── Текстовые документы ───
        if mime.startswith("text/") or file_name.lower().endswith((".txt", ".md", ".json", ".csv")):
            try:
                data = await message.download_media(bytes)
                text = data.decode("utf-8", errors="ignore")[:2000]
                return f"[Документ {file_name}]\n{text}"
            except Exception as e:
                return f"[Документ: ошибка — {e}]"

    return ""