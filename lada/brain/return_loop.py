# return_loop.py — возврат к непрочитанным сообщениям.
#
# FIX: wm_block теперь вызывается с user_id (раньше падало с
# "as_context_block() missing 1 required positional argument: 'user_id'").

import asyncio
import random
from datetime import datetime

from config import MODEL, MAX_HISTORY, INITIATIVE_ENABLED

from lada.core import users_names
from lada.core.memory import get_history, add_message
from lada.core.working_memory import as_context_block as wm_block
from lada.core import unread
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core.persona import get_persona
from lada.core.llm_json import chat_json
from lada.brain import mood, sins, image_policy
from lada.brain.relationship import get_status_label


RETURN_MIN_HOURS = 3.0
RETURN_COOLDOWN_HOURS = 6.0
RETURN_CHANCE = 0.55
CHECK_INTERVAL_MINUTES = 30


RETURN_PROMPT = (
    "Ты — Лада. Человек писал тебе, но ты не ответила. Возможно, была занята, "
    "возможно, просто не хотела. Сейчас ты видишь его сообщения и можешь "
    "вернуться к разговору.\n\n"
    "Верни СТРОГО JSON:\n"
    "{\n"
    '  "act": true или false,\n'
    '  "reason": "почему решила вернуться или не возвращаться",\n'
    '  "message": "текст сообщения 1-3 предложения или пустая строка"\n'
    "}\n\n"
    "ПРАВИЛА:\n"
    "- Верни act=true, если у тебя есть что сказать по сути его сообщений.\n"
    "- Если хочешь вернуться — можешь коротко извиниться («сорри, залипла»), "
    "но НЕ пиши длинных извинений и не оправдывайся.\n"
    "- Ответь на его вопрос / отреагируй на его мысль. Не «привет, писал?».\n"
    "- Если сейчас не хочешь — act=false, и ты вернёшься к этому позже.\n"
    "- НЕ пиши про дождь и погоду, если он не поднимал эту тему.\n\n"
    "ХОРОШИЕ ПРИМЕРЫ:\n"
    "«я тут. сорри, залипла. ты про X спрашивал? смотри...»\n"
    "«о, ты писал. да, я помню про Y. расскажу вечером.»\n"
    "«блин, забыла ответить. слушаю.»\n"
    "«да, я согласна. только давай не сегодня, я никакая.»\n\n"
    "ПЛОХИЕ ПРИМЕРЫ:\n"
    "«Привет! Извини, что долго не отвечала, у меня были дела.»\n"
    "«Прошу прощения за задержку. Чем могу помочь?»\n"
    "«Привет, как дела?»\n\n"
    "Если он написал глупость или провокацию — можешь вообще не отвечать "
    "(act=false)."
)


def _build_context(user_id: int) -> str:
    name = users_names.display_name(user_id)
    pending = unread.get_pending(user_id)

    parts = [f"Пользователь: {name} (ID {user_id})."]
    parts.append(f"Настроение: {mood.get_mood()}.")
    parts.append(f"Статус: {get_status_label(user_id)}.")

    # FIX: передаём user_id — раньше падало с TypeError
    wm = wm_block(user_id)
    if wm:
        parts.append("")
        parts.append(wm)

    parts.append("")
    parts.append("=== ЕГО СООБЩЕНИЯ, НА КОТОРЫЕ ТЫ НЕ ОТВЕТИЛА ===")
    for m in pending[-10:]:
        ts = m.get("ts", "")[:16].replace("T", " ")
        parts.append(f"  [{ts}] {m['text'][:250]}")

    history = get_history(user_id, limit=6)
    if history:
        parts.append("")
        parts.append("=== ЧТО ВЫ ОБСУЖДАЛИ ДО ЭТОГО ===")
        for m in history:
            role = "Он" if m["role"] == "user" else "Ты"
            parts.append(f"  {role}: {(m.get('content') or '')[:150]}")

    obs = [
        m for m in faiss_mem.metadata
        if m.get("user_id") == user_id and m.get("category") == "observation"
    ][-5:]
    if obs:
        parts.append("")
        parts.append("=== ЧТО ТЫ ПОМНИШЬ О НЁМ ===")
        for m in obs:
            parts.append(f"- {m['text'][:200]}")

    return "\n".join(parts)


async def try_return(client, user_id: int) -> bool:
    if not INITIATIVE_ENABLED:
        return False

    pending = unread.get_pending(user_id)
    if not pending:
        return False

    since = unread.hours_since_last_unread(user_id)
    if since < RETURN_MIN_HOURS:
        return False

    since_attempt = unread.hours_since_last_return_attempt(user_id)
    if since_attempt < RETURN_COOLDOWN_HOURS:
        return False

    m = mood.get_mood()
    if m in ("раздражённая", "расстроенная", "холодная"):
        return False

    state = sins.get_state()
    if state.get("гнев", 50) > 75:
        return False

    if random.random() > RETURN_CHANCE:
        return False

    context = _build_context(user_id)
    messages = [
        {"role": "system", "content": get_persona() + "\n\n" + RETURN_PROMPT},
        {"role": "user", "content": context},
    ]

    data = await chat_json(messages, "return")
    unread.touch_return_attempt(user_id)

    if not isinstance(data, dict):
        return False

    act = bool(data.get("act"))
    message = (data.get("message") or "").strip()
    reason = (data.get("reason") or "").strip()

    if not act or not message or len(message) < 3:
        print(f"   💤 Возврат → {user_id}: не сейчас ({reason[:80]})")
        return False

    try:
        await client.send_message(user_id, message)
    except Exception as e:
        print(f"   ⚠️ Возврат не отправлен: {e}")
        return False

    try:
        add_message(user_id, "assistant", message, max_history=MAX_HISTORY)
    except Exception as e:
        print(f"   ⚠️ Не записал в историю: {e}")

    unread.mark_read(user_id)

    faiss_mem.add(
        message,
        category="return",
        source="autonomous",
        user_id=user_id,
    )

    print(f"   ↩️  Возврат → {user_id}: {message[:80]}")
    print(f"      Причина: {reason[:100]}")
    return True


async def return_loop(client):
    if not INITIATIVE_ENABLED:
        print("   ⏸ return_loop выключен (INITIATIVE_ENABLED=False)")
        return

    await asyncio.sleep(180)
    while True:
        try:
            users = unread.list_users_with_pending()
            if users:
                print(f"\n↩️  [{datetime.now().strftime('%H:%M:%S')}] "
                      f"Проверка возвратов: {len(users)} юзер(ов)")
            for uid in users:
                try:
                    await try_return(client, uid)
                except Exception as e:
                    print(f"   ⚠️ Возврат к {uid}: {e}")
        except Exception as e:
            print(f"⚠️ return_loop: {e}")

        await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)