# handlers.py — обработчики Telegram-событий

import asyncio
import re
from functools import partial

from telethon import events

from config import (
    OWNER_ID, MAX_HISTORY,
    FAISS_TOP_K, FAISS_MIN_SCORE, FAISS_MAX_CHARS,
    WEB_SEARCH_ENABLED, WEB_SEARCH_MAX_ROUNDS,
    EMOTION_ENABLED, COOLDOWN_SECONDS,
)
from lada.core import users_names, user_city
from lada.core.llm import ask, ask_text
from lada.core.memory import get_history, add_message
from lada.core.faiss_memory import faiss_mem
from lada.brain import mood, image_policy, image_mood, will, relationship as rel
from lada.brain.relationship import (
    touch_first_seen, get_status, get_pending,
    accept_proposal, reject_proposal, record_proposal,
)
from lada.brain.emotions import evaluate_and_record
from lada.brain.initiative import touch_user_activity
from lada.brain.monologue import get_recent_thoughts
from lada.telegram.mode import should_reply
from lada.telegram.media_processor import process_media
from lada.telegram.cooldown import is_allowed, touch
from lada.telegram.presence import set_online, touch_activity
from lada.telegram.queue import QueuedMessage
from lada.telegram import splitter, stickers
from lada.tools import web_search
from lada.tools import image_gen
from lada.app.client import get_client, message_queue, recent_photo_prompts
from lada.app.utils import (
    log, log_thinking, detect_yes_no, strip_asterisk_actions,
)
from lada.app.state_brief import build_state_brief
from lada.app.commands import handle_owner_command


IMAGE_TRIGGERS = [
    "селфи", "selfie", "покажи себя", "покажись",
    "пришли фото", "пришли фотку", "покажи фото",
    "как ты выглядишь", "сфоткайся", "сделай фото",
    "сделай селфи", "покажи как ты", "где ты сейчас",
    "покажи мусю", "покажи кошку", "фото кота", "фото кошки",
]

SEARCH_TRIGGERS = [
    "поищи", "погугли", "загугли", "найди в интернете",
    "проверь в интернете", "посмотри в интернете",
    "что в интернете", "найди информацию", "поиск в интернете",
]


@events.register(events.NewMessage(incoming=True))
async def handler(event):
    client = get_client()
    user_id = event.sender_id
    text = event.raw_text or ""

    touch_user_activity(user_id)
    touch_first_seen(user_id)

    if user_id == OWNER_ID and text.startswith("/"):
        try:
            await client.send_read_acknowledge(event.chat_id)
        except Exception:
            pass
        if await handle_owner_command(text, event):
            return

    if not should_reply(user_id, OWNER_ID):
        return

    if not text and not event.media:
        return

    if user_id != OWNER_ID:
        allowed, wait = is_allowed(user_id, COOLDOWN_SECONDS)
        if not allowed:
            log(f"⏸ Cooldown: {users_names.display_name(user_id)}, ждать {wait}с")
            return
        touch(user_id)

    try:
        sender = await event.get_sender()
        users_names.update(
            user_id,
            username=getattr(sender, "username", "") or "",
            first_name=getattr(sender, "first_name", "") or "",
            last_name=getattr(sender, "last_name", "") or "",
            phone=getattr(sender, "phone", "") or "",
        )
    except Exception as e:
        log(f"⚠️ Не удалось получить данные отправителя {user_id}: {e}")

    user_display = users_names.display_name(user_id)

    qm = QueuedMessage(
        user_id=user_id,
        chat_id=event.chat_id,
        event=event,
        text=(text or "")[:300],
        username=user_display,
    )
    ok = await message_queue.put(qm)
    if ok:
        log(f"📬 +в очередь от {user_display} (всего: {message_queue.size()})")
    else:
        log(f"⚠️ Очередь переполнена, сообщение от {user_display} потеряно")


async def process_batch(batch: list[QueuedMessage]):
    if not batch:
        return

    client = get_client()

    user_id = batch[0].user_id
    chat_id = batch[0].chat_id
    user_display = users_names.display_name(user_id)

    log(f"📥 Из очереди: {len(batch)} сообщ. от {user_display} (осталось: {message_queue.size()})")

    touch_activity()
    await set_online(client)
    try:
        await client.send_read_acknowledge(chat_id)
    except Exception as e:
        log(f"⚠️ Read: {e}")

    parts: list[str] = []
    for qm in batch:
        text = (qm.event.raw_text or "").strip()
        if text:
            parts.append(text)
        if qm.event.media:
            try:
                media_desc = await process_media(qm.event.message)
                if media_desc:
                    parts.append(media_desc)
            except Exception as e:
                log(f"⚠️ Медиа: {e}")

    combined = "\n\n".join(parts).strip()
    if not combined:
        return

    if not user_city.get_city(user_id):
        city_match = re.search(
            r'(?:я\s+из|живу\s+в|нахожусь\s+в|пишу\s+из)\s+([А-ЯЁ][а-яё\-]+(?:\s+[А-ЯЁ][а-яё\-]+)?)',
            combined,
        )
        if city_match:
            detected = city_match.group(1).strip()
            user_city.set_city(user_id, detected)
            log(f"   🏙 Автоопределён город: {detected}")

    pending = get_pending(user_id)
    if pending:
        answer = detect_yes_no(combined)
        if answer == "yes":
            result = accept_proposal(user_id)
            if result:
                log(f"   💗 Предложение '{result}' ПРИНЯТО")
                add_message(
                    user_id, "system",
                    f"[СИСТЕМА] Пользователь согласился стать твоим "
                    f"{'мужем' if result == 'husband' else 'любовником'}.",
                    max_history=MAX_HISTORY,
                )
        elif answer == "no":
            result = reject_proposal(user_id)
            if result:
                log(f"   💔 Предложение '{result}' ОТКЛОНЕНО")
                add_message(
                    user_id, "system",
                    f"[СИСТЕМА] Пользователь отказался стать твоим "
                    f"{'мужем' if result == 'husband' else 'любовником'}. "
                    f"Тебе больно. Кулдаун месяц.",
                    max_history=MAX_HISTORY,
                )
        else:
            log(f"   ⏳ Предложение '{pending}' ожидает, ответ неоднозначный")

    add_message(user_id, "user", combined, max_history=MAX_HISTORY)
    history = get_history(user_id, limit=MAX_HISTORY)

    full_history_count = len(get_history(user_id))

    candidates = faiss_mem.search(
        combined,
        k=FAISS_TOP_K,
        user_id=user_id,
        min_score=FAISS_MIN_SCORE,
        max_chars=FAISS_MAX_CHARS,
    )
    related = [r for r in candidates if r.get("category") != "emotion"]
    if related:
        knowledge_text = "\n".join(f"- {k['text']}" for k in related)
        history = history + [{
            "role": "system",
            "content": (
                f"Вот что ты помнишь (про этого пользователя и в целом):\n"
                f"{knowledge_text}"
            )
        }]
        log(f"   🧠 FAISS: {len(related)} записей, {len(knowledge_text)} симв.")
    else:
        log(f"   🧠 FAISS: пусто")

    user_requested_photo = any(t in combined.lower() for t in IMAGE_TRIGGERS)

    force_search = WEB_SEARCH_ENABLED and any(
        trigger in combined.lower() for trigger in SEARCH_TRIGGERS
    )

    user_explicit_nsfw = image_policy.is_explicit_nsfw_request(combined)

    brief = build_state_brief(
        user_id, combined, user_display,
        current_msg_count=full_history_count,
        user_requested_photo=user_requested_photo,
    )
    history = history + [{"role": "system", "content": brief}]

    thoughts = get_recent_thoughts()
    if thoughts:
        thoughts_text = "\n".join(f"- {t['text'][:150]}" for t in thoughts)
        history = history + [{
            "role": "system",
            "content": (
                "Недавно ты думала про себя:\n"
                f"{thoughts_text}\n\n"
                "(Это твои личные мысли. Не упоминай их напрямую.)"
            )
        }]

    reply = ""
    thinking = ""
    round_num = 0

    async with client.action(chat_id, "typing"):
        if force_search:
            log(f"   🔍 Принудительный поиск: {combined[:80]}")
            search_result = await asyncio.to_thread(
                web_search.search_and_format, combined
            )
            history = history + [
                {"role": "system", "content": search_result},
                {"role": "system",
                 "content": "Пользователь попросил найти информацию. Используй результаты."},
            ]

        while round_num <= WEB_SEARCH_MAX_ROUNDS:
            reply, thinking = await asyncio.to_thread(ask, history)

            if WEB_SEARCH_ENABLED and "[SEARCH]" in reply and not force_search:
                match = re.search(r'\[SEARCH\]\s*(.+)', reply)
                if match:
                    query = match.group(1).strip()
                    if query:
                        log(f"   🔍 Веб-поиск: {query}")
                        search_result = await asyncio.to_thread(
                            web_search.search_and_format, query
                        )
                        history = history + [
                            {"role": "assistant", "content": reply},
                            {"role": "system", "content": search_result},
                            {"role": "system",
                             "content": "Теперь дай финальный ответ. Не пиши [SEARCH] снова."},
                        ]
                        round_num += 1
                        continue
            break

    log_thinking(thinking)

    if "[SEARCH]" in reply:
        reply = "🤔 Не нашла точный ответ."

    # ─── Срезаем *действия* ───
    reply, was_stripped = strip_asterisk_actions(reply)
    if was_stripped:
        log("   ✂️  Срезаны *действия* из ответа")

    if "[SILENCE]" in reply:
        log("   🤐 Модель решила промолчать.")
        add_message(user_id, "assistant", "[SILENCE]", max_history=MAX_HISTORY)
        return

    propose_match = re.search(r'\[PROPOSE:\s*(lover|husband)\s*\]', reply)
    if propose_match and not get_pending(user_id) and get_status(user_id) not in rel.SPECIAL_STATUSES:
        which = propose_match.group(1)
        record_proposal(user_id, which)
        reply = reply.replace(propose_match.group(0), "").strip()
        log(f"   💗 Модель предложила отношения: {which}")

    sticker_match = re.search(r'\[STICKER(?::\s*([^\]]+))?\]', reply)
    sticker_emoji = None
    need_sticker = False

    if sticker_match:
        need_sticker = True
        sticker_emoji = sticker_match.group(1)
        if sticker_emoji:
            sticker_emoji = sticker_emoji.strip()
        reply = reply.replace(sticker_match.group(0), "").strip()
        log(f"   🎭 Модель хочет стикер (emoji={sticker_emoji or 'по настроению'})")

    image_hint = None
    image_subject = "lada"

    match = re.search(r'\[IMAGE\]\s*pet:\s*(.+)', reply)
    if match:
        image_hint = match.group(1).strip()
        image_subject = "pet"
        reply = reply.replace(match.group(0), "").strip()
    else:
        match = re.search(r'\[IMAGE\]\s*(?:hint:\s*)?(.+)', reply)
        if match:
            image_hint = match.group(1).strip()
            reply = reply.replace(match.group(0), "").strip()

    if not image_hint and user_requested_photo:
        log("   ℹ️ Пользователь просил фото, модель не написала [IMAGE] — отказ")

    image_prompt = None
    if image_hint:
        if image_subject == "pet":
            image_prompt = image_hint
        else:
            image_prompt = image_mood.build_scene(
                image_hint, mood.get_mood(), user_id, recent_photo_prompts
            )

    if image_prompt and image_subject == "lada":
        allowed, reason = image_policy.check_allowed(
            user_id, image_prompt,
            source="chat",
            user_explicit_nsfw=user_explicit_nsfw,
        )
        log(f"   🔒 Политика: {image_policy.debug_state(user_id, image_prompt)}")
        if not allowed:
            log(f"   🚫 Отказ политики: {reason}")
            image_prompt = None
            if not reply.strip():
                reply = image_policy.explain_refusal(user_id, image_prompt)

    if not reply.strip() and not need_sticker:
        if image_prompt:
            reply = "Сейчас, секунду."
        else:
            log("   ⚠️ Пустой ответ, прошу заново")
            history_retry = history + [
                {"role": "assistant", "content": reply},
                {"role": "system",
                 "content": "Ты не написала ответ. Напиши короткий ответ пользователю."},
            ]
            reply = await asyncio.to_thread(ask_text, history_retry)
            if not reply.strip():
                reply = "⚠️ Что-то у меня сбой. Повтори."

    if image_prompt:
        log(f"   🎨 Итоговый промпт ({image_subject}): {image_prompt[:140]}")

    add_message(user_id, "assistant", reply, max_history=MAX_HISTORY)

    if need_sticker:
        try:
            sent = await stickers.send_sticker_by_mood(
                client, chat_id,
                mood=mood.get_mood(),
                force_emoji=sticker_emoji,
            )
            if sent:
                log(f"   🎭 Стикер отправлен (mood={mood.get_mood()})")
        except Exception as e:
            log(f"   ⚠️ Стикер не ушёл: {e}")

    if reply.strip():
        try:
            sent_msgs = await splitter.send_in_parts(client, chat_id, reply)
            log(f"✅ Ответил ({user_display}, частей: {len(sent_msgs)})")
        except Exception as e:
            log(f"⚠️ Не удалось отправить: {e}")

    if image_prompt:
        log(f"   🎨 Генерация ({image_subject}): {image_prompt[:100]}...")

        if image_subject == "lada":
            extra_neg = image_mood.get_negative_for_mood(mood.get_mood(), user_id)
            neg = image_gen.BASE_NEGATIVE + (", " + extra_neg if extra_neg else "")
        else:
            neg = image_gen.PET_NEGATIVE

        async with client.action(chat_id, "photo"):
            img_path = await asyncio.to_thread(
                partial(
                    image_gen.generate,
                    image_prompt,
                    negative_prompt=neg,
                    subject=image_subject,
                )
            )

        if img_path:
            try:
                await client.send_file(chat_id, img_path)
                log(f"   🖼 Изображение отправлено ({image_subject})")
                recent_photo_prompts.append(image_prompt)
                if len(recent_photo_prompts) > 10:
                    recent_photo_prompts.pop(0)
                will.touch_photo_sent(
                    user_id,
                    len(get_history(user_id)),
                    image_prompt,
                )
                faiss_mem.add(
                    f"[Сгенерировала фото] {image_prompt}",
                    category="image",
                    source="image_gen",
                    user_id=user_id,
                )
            except Exception as e:
                log(f"   ⚠️ Не удалось отправить изображение: {e}")

    if EMOTION_ENABLED and not image_prompt:
        asyncio.create_task(evaluate_and_record(combined, reply, user_id))
    elif image_prompt:
        log("   ⏭ Эмоции пропущены (VRAM занят после генерации)")