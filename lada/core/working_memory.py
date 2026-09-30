# working_memory.py — краткосрочная рабочая память Лады.
#
# Идея из alex2772/kuni: держим отдельный файл (data/working_memory.md)
# с тем, что важно СЕЙЧАС (1-3 дня). Он всегда подмешивается в промпт,
# и обновляется после каждого диалога через LLM.
#
# Отличие от FAISS: FAISS — долгосрочный архив, WM — оперативка.
# В WM нет дублей, нет мусора, только активные темы и обещания.

import asyncio
from datetime import datetime
from pathlib import Path

import ollama

from config import MODEL

WM_FILE = Path("data") / "working_memory.md"
WM_FILE.parent.mkdir(exist_ok=True)

MAX_CHARS = 4000
MAX_LINES = 30


WM_UPDATE_PROMPT = (
    "Ты — редактор рабочей памяти Лады. Это короткий список того, "
    "что сейчас важно в её жизни и общении с людьми.\n\n"
    "В рабочей памяти живут:\n"
    "- незавершённые темы и обещания (кто что обещал, кто о чём хотел поговорить)\n"
    "- важные свежие факты о собеседниках (работа, планы, события, настроение)\n"
    "- активные договорённости («должен ответить», «обещала рассказать»)\n"
    "- то, что ты держишь в голове в последние 1-3 дня\n\n"
    "ФОРМАТ:\n"
    "- markdown, группируй по пользователям: «### ID 123 (Имя)»\n"
    "- под каждым — пункты через «- »\n"
    "- максимум 30 строк всего\n\n"
    "ПРАВИЛА:\n"
    "- УДАЛЯЙ то, что уже неактуально или старше 3 дней.\n"
    "- НЕ дублируй похожие пункты — объединяй в один.\n"
    "- НЕ пиши общих вещей («человек человек», «мы общались»).\n"
    "- НЕ пиши шаблонных фраз («как дела», «хорошего дня»).\n"
    "- Если ничего важного не появилось — оставь как было.\n\n"
    "Выведи ТОЛЬКО новый текст рабочей памяти (markdown), без пояснений."
)


def load() -> str:
    if WM_FILE.exists():
        try:
            return WM_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""


def save(text: str):
    text = (text or "").strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    WM_FILE.write_text(text, encoding="utf-8")


def _is_low_quality(text: str) -> bool:
    """Отсекаем пустое, слишком короткое, откровенный мусор."""
    if not text or len(text) < 20:
        return True
    low = text.lower().strip()
    if low in ("без изменений", "unchanged", "(пусто)", "пусто"):
        return True
    return False


async def update_for_dialog(
    user_id: int,
    user_msg: str,
    bot_reply: str,
    user_display: str = "",
) -> None:
    """
    Обновляет WM после диалога. Ошибки глотаем — это фон.
    Вызывается через asyncio.create_task, не блокирует ответ.
    """
    current = load()

    name_hint = f" ({user_display})" if user_display else ""
    user_block = (
        f"НОВЫЙ ДИАЛОГ с пользователем ID {user_id}{name_hint}:\n"
        f"Пользователь: {user_msg[:800]}\n"
        f"Лада: {bot_reply[:800]}\n"
    )

    messages = [
        {"role": "system", "content": WM_UPDATE_PROMPT},
        {"role": "user", "content": (
            f"ТЕКУЩАЯ РАБОЧАЯ ПАМЯТЬ:\n{current or '(пусто)'}\n\n"
            f"{user_block}\n"
            f"Сегодня: {datetime.now().strftime('%Y-%m-%d %H:%M')}.\n\n"
            f"Обнови рабочую память: добавь важное из диалога, "
            f"убери устаревшее, объедини похожее."
        )},
    ]

    try:
        resp = await asyncio.to_thread(
            ollama.chat,
            model=MODEL,
            messages=messages,
            options={"temperature": 0.3, "num_predict": 700},
        )
    except Exception as e:
        print(f"   ⚠️ WM: {e}")
        return

    msg = resp.get("message", {})
    text = (msg.get("content") or "").strip()
    if not text:
        text = (msg.get("thinking") or "").strip()[-MAX_CHARS:]

    if _is_low_quality(text):
        return

    save(text)
    lines = text.count("\n") + 1
    print(f"   📝 WM обновлена ({len(text)} симв., {lines} строк)")


def as_context_block() -> str:
    """Блок для вставки в промпт. Пустая строка если WM пуста."""
    text = load()
    if not text:
        return ""
    return (
        "=== РАБОЧАЯ ПАМЯТЬ (что у тебя сейчас на уме) ===\n"
        f"{text}\n"
        "=== КОНЕЦ РАБОЧЕЙ ПАМЯТИ ===\n\n"
        "(Это твоя краткосрочная память о последних днях. "
        "Используй как контекст, но не цитируй дословно.)"
    )


def clear():
    if WM_FILE.exists():
        WM_FILE.unlink()