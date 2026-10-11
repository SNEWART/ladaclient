# working_memory.py — краткосрочная рабочая память (per-user).

import asyncio
from datetime import datetime

import ollama

from config import MODEL
from lada.core.db import get_conn, Transaction


MAX_CHARS = 4000


WM_UPDATE_PROMPT = (
    "Ты — редактор рабочей памяти Лады. Это короткий список того, "
    "что сейчас важно в её общении с КОНКРЕТНЫМ человеком.\n\n"
    "В рабочей памяти живут:\n"
    "- незавершённые темы и обещания\n"
    "- важные свежие факты о собеседнике\n"
    "- активные договорённости\n"
    "- то, что ты держишь в голове по нему за последние 1-3 дня\n\n"
    "ФОРМАТ:\n"
    "- ОДИН заголовок: «### ID <user_id> (<имя>)»\n"
    "- под ним — пункты через «- »\n"
    "- максимум 20 строк всего\n\n"
    "ПРАВИЛА:\n"
    "- Это память ТОЛЬКО об этом человеке.\n"
    "- УДАЛЯЙ устаревшее (старше 3 дней).\n"
    "- НЕ дублируй похожие пункты.\n"
    "- НЕ пиши общих вещей и шаблонов.\n"
    "- Если ничего важного — оставь как было.\n\n"
    "Выведи ТОЛЬКО новый текст рабочей памяти (markdown)."
)


def load(user_id: int) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT content FROM working_memory WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row and row["content"]:
        return row["content"].strip()
    return ""


def save(user_id: int, text: str):
    text = (text or "").strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        conn.execute(
            "INSERT INTO working_memory (user_id, content, updated) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET content = excluded.content, "
            "updated = excluded.updated",
            (user_id, text, ts),
        )


def clear(user_id: int):
    with Transaction() as conn:
        conn.execute("DELETE FROM working_memory WHERE user_id = ?",
                     (user_id,))


def clear_all():
    with Transaction() as conn:
        conn.execute("DELETE FROM working_memory")


def _is_low_quality(text: str) -> bool:
    if not text or len(text) < 20:
        return True
    low = text.lower().strip()
    if low in ("без изменений", "unchanged", "(пусто)", "пусто"):
        return True
    return False


async def update_for_dialog(user_id: int, user_msg: str, bot_reply: str,
                            user_display: str = "") -> None:
    current = load(user_id)

    name_hint = f" ({user_display})" if user_display else ""
    user_block = (
        f"НОВЫЙ ДИАЛОГ с пользователем ID {user_id}{name_hint}:\n"
        f"Пользователь: {user_msg[:800]}\n"
        f"Лада: {bot_reply[:800]}\n"
    )

    messages = [
        {"role": "system", "content": WM_UPDATE_PROMPT},
        {"role": "user", "content": (
            f"ТЕКУЩАЯ РАБОЧАЯ ПАМЯТЬ ОБ ЭТОМ ЧЕЛОВЕКЕ (ID {user_id}):\n"
            f"{current or '(пусто)'}\n\n"
            f"{user_block}\n"
            f"Сегодня: {datetime.now().strftime('%Y-%m-%d %H:%M')}.\n\n"
            f"Обнови рабочую память ТОЛЬКО об этом человеке (ID {user_id}). "
            f"Не добавляй сюда факты о других пользователях."
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

    save(user_id, text)
    lines = text.count("\n") + 1
    print(f"   📝 WM обновлена для {user_id} "
          f"({len(text)} симв., {lines} строк)")


def as_context_block(user_id: int) -> str:
    text = load(user_id)
    if not text:
        return ""
    return (
        "=== РАБОЧАЯ ПАМЯТЬ (что у тебя сейчас на уме про ЭТОГО человека) ===\n"
        f"{text}\n"
        "=== КОНЕЦ РАБОЧЕЙ ПАМЯТИ ===\n\n"
        "(Это твоя краткосрочная память о нём за последние дни. "
        "Используй как контекст, но не цитируй дословно.)"
    )