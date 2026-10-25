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
from lada.core import unread
from lada.core.llm import ask, ask_text
from lada.core.memory import get_history, add_message, add_silence_marker
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core import memory_search
from lada.core.working_memory import update_for_dialog as wm_update
from lada.brain import mood, image_policy, image_mood, will, relationship as rel
from lada.brain import night_policy
from lada.brain.relationship import (
    touch_first_seen, get_status, get_pending,
    accept_proposal, reject_proposal, record_proposal,
)
from lada.brain.combined_eval import evaluate_and_update
from lada.brain.initiative import touch_user_activity
from lada.brain.monologue import get_recent_thoughts
from lada.telegram.mode import should_reply
from lada.telegram.media_processor import process_media
from lada.telegram.cooldown import is_allowed, touch
from lada.telegram.presence import set_online, touch_activity
from lada.telegram.queue import QueuedMessage
from lada.telegram import splitter, stickers
from lada.tools import web_search
from lada.tools import search_agent
from lada.tools import image_gen
from lada.app.client import get_client, message_queue, recent_photo_prompts
from lada.app.utils import (
    log, log_thinking, detect_yes_no,
    strip_asterisk_actions, strip_question_echo, strip_fake_notes,
)
from lada.app.state_brief import build_state_brief
from lada.app.commands import handle_owner_command


IMAGE_TRIGGERS = [
    "селфи", "selfie", "покажи себя", "покажись",
    "пришли фото", "пришли фотку", "покажи фото",
    "как ты выглядишь", "сфоткайся", "сделай фото",
    "сделай селфи", "покажи как ты", "где ты сейчас",
    "покажи мусю", "покажи кошку", "фото кота", "фото кошки",
    "показать мусю", "показать кошку", "показать мне мусю",
    "показать мне кошку", "показать фото", "показать мне фото",
    "показать себя", "показать мне себя",
    "хочу увидеть мусю", "хочу увидеть кошку", "хочу увидеть тебя",
    "хочу посмотреть мусю", "хочу посмотреть кошку",
    "можно фото", "можно мусю", "можно кошку", "можно тебя",
]

SEARCH_TRIGGERS = [
    "поищи", "погугли", "загугли", "найди в интернете",
    "проверь в интернете", "посмотри в интернете",
    "что в интернете", "найди информацию", "поиск в интернете",
]

EMOTIONAL_TOP_K = 12
EMOTIONAL_MAX_CHARS = 4000

IGNORED_USER_IDS = {777000, 7770000}


def _he_worries_you_are_gone(msg: str) -> bool:
    if not msg:
        return False
    low = msg.lower()
    triggers = [
        "ты пропала", "куда пропала", "ты где", "ты тут",
        "ты не отвечаешь", "куда делась", "ты ещё здесь",
        "ты молчишь", "алло", "ты жива", "не отвечала",
        "пропала куда", "не отвечала долго", "ты вообще тут",
    ]
    return any(t in low for t in triggers)


def _user_requested_photo(text: str) -> bool:
    low = text.lower()
    for t in IMAGE_TRIGGERS:
        if t in low:
            return True
    has_object = any(w in low for w in
                     ("мусю", "муся", "муси", "кошк", "кот", "фото",
                      "фотк", "селфи", "себя", "картинк"))
    has_request = any(w in low for w in
                      ("покаж", "пришл", "скинь", "хочу", "можно",
                       "дай", "сделай", "сфотк", "увидеть", "посмотреть",
                       "глянуть", "взглянуть"))
    has_marker = "?" in text
    return has_object and (has_request or has_marker)


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


async def process_batch(batch: list):
    if not batch:
        return

    client = get_client()

    user_id = batch[0].user_id
    chat_id = batch[0].chat_id

    if user_id in IGNORED_USER_IDS:
        log(f"⏭ Служебный ID {user_id}, пропускаю")
        return

    user_display = users_names.display_name(user_id)

    log(f"📥 Из очереди: {len(batch)} сообщ. от {user_display} "
        f"(осталось: {message_queue.size()})")

    # Если он ответил — сбрасываем счётчик инициатив
    try:
        from lada.brain import initiative as _init
        _init.on_user_replied(user_id)
    except Exception:
        pass

    touch_activity()
    await set_online(client)
    try:
        await client.send_read_acknowledge(chat_id)
    except Exception as e:
        log(f"⚠️ Read: {e}")

    parts: list = []
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

    if night_policy.detect_revoke_request(combined):
        if night_policy.get_night_ok(user_id) == 1:
            night_policy.revoke_night(user_id)
            log(f"   🌙 Разрешение писать ночью отозвано")

    if night_policy.get_night_pending(user_id):
        answer = detect_yes_no(combined)
        if answer == "yes":
            night_policy.accept_night(user_id)
            log(f"   🌙 Разрешение писать ночью: ДА")
            add_message(
                user_id, "system",
                "[СИСТЕМА] Пользователь разрешил тебе писать ему ночью. "
                "Пользуйся этим мягко, не злоупотребляй.",
                max_history=MAX_HISTORY,
            )
        elif answer == "no":
            night_policy.reject_night(user_id)
            log(f"   🌙 Разрешение писать ночью: НЕТ")
            add_message(
                user_id, "system",
                "[СИСТЕМА] Пользователь не разрешил писать ночью. "
                "Не пиши ему ночью и не спрашивай об этом снова.",
                max_history=MAX_HISTORY,
            )

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

    history_for_search = get_history(user_id, limit=6)

    add_message(user_id, "user", combined, max_history=MAX_HISTORY)
    history = get_history(user_id, limit=MAX_HISTORY)

    full_history_count = len(get_history(user_id))

    candidates = memory_search.search_emotional(
        combined,
        user_id=user_id,
        k=EMOTIONAL_TOP_K,
        max_chars=EMOTIONAL_MAX_CHARS,
        min_score=0.25,
    )

    if candidates:
        from lada.core.memory_hierarchy import format_hierarchical
        hier_text = format_hierarchical(candidates)
        if hier_text:
            history = history + [{"role": "system", "content": hier_text}]
            n_facts = sum(1 for c in candidates
                          if c.get("category") == "observation"
                          and c.get("valence") is None)
            n_emo = sum(1 for c in candidates
                        if c.get("category") == "emotion")
            log(f"   🧠 память: {len(candidates)} записей "
                f"(факты≈{n_facts}, эмоции≈{n_emo}), "
                f"{len(hier_text)} симв.")
        else:
            log(f"   🧠 память: пусто после раскладки")
    else:
        log(f"   🧠 память: пусто")

    user_requested_photo = _user_requested_photo(combined)

    force_search = WEB_SEARCH_ENABLED and any(
        trigger in combined.lower() for trigger in SEARCH_TRIGGERS
    )

    user_explicit_nsfw = image_policy.is_explicit_nsfw_request(combined)

    brief = build_state_brief(
        user_id, combined, user_display,
        current_msg_count=full_history_count,
        user_requested_photo=user_requested_photo,
    )
    if brief.get("facts"):
        history = history + [{"role": "system", "content": brief["facts"]}]
    _style_brief = brief.get("style", "")

    # Внедрение pending инициативы
    try:
        from lada.brain import pending as pending_mod
        pending_data = pending_mod.get_pending(user_id)
        if pending_data:
            pending_block = (
                f"=== У ТЕБЯ БЫЛА ЗАГОТОВКА ===\n"
                f"Ты собиралась написать: «{pending_data['message']}»\n"
                f"Причина: {pending_data['reason']}\n"
                f"Сейчас он написал сам. Можешь упомянуть, что думала ему написать, "
                f"и вплести эту мысль — если уместно. Не используй штампы."
            )
            history = history + [{"role": "system", "content": pending_block}]
            pending_mod.delete_pending(user_id)
            log("   📬 Pending инициатива вплетена в контекст и удалена")
    except Exception as e:
        log(f"   ⚠️ pending injection: {e}")

    auto_search_block = None
    if (WEB_SEARCH_ENABLED
            and not force_search
            and search_agent.available()):
        decision = await asyncio.to_thread(
            search_agent.decide_search, combined, history_for_search
        )
        if decision.get("search") and decision.get("query"):
            log(f"   🔎 Search agent → «{decision['query']}» "
                f"({decision.get('reason', '')[:70]})")
            auto_search_block = await asyncio.to_thread(
                web_search.search_and_format, decision["query"]
            )
        else:
            log(f"   🔎 Search agent: не нужен "
                f"({decision.get('reason', '')[:70]})")

    if auto_search_block:
        if "[SEARCH_FAILED]" in auto_search_block:
            history = history + [
                {"role": "system", "content": auto_search_block},
                {"role": "system",
                 "content": (
                     "🔥 КРИТИЧНО: поиск НЕ ДАЛ результатов. "
                     "Ты ОБЯЗАНА честно сказать «ничего конкретного не нашла». "
                     "КАТЕГОРИЧЕСКИ запрещено выдумывать."
                 )},
            ]
        else:
            history = history + [
                {"role": "system", "content": auto_search_block},
                {"role": "system",
                 "content": (
                     "🔥 ВАЖНО: используй ТОЛЬКО конкретные факты из "
                     "результатов поиска. Отвечай 2-3 фактами."
                 )},
            ]

    unread_block = unread.as_context_block(user_id)
    if unread_block:
        history = history + [{"role": "system", "content": unread_block}]
        log(f"   ⏳ Unread-блок: {unread.count(user_id)} сообщ.")

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

    if _style_brief:
        history = history + [{"role": "system", "content": _style_brief}]

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
                        log(f"   🔍 Веб-поиск (модель): {query}")
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

    _before = reply
    reply = strip_question_echo(reply, combined)
    if reply != _before:
        log(f"   ✂️  Срезано эхо вопроса: «{_before[:40]}…»")

    reply, fake_stripped = strip_fake_notes(reply)
    if fake_stripped:
        log("   ✂️  Срезаны псевдо-пометки [отправила …] из ответа")

    reply, was_stripped = strip_asterisk_actions(reply)
    if was_stripped:
        log("   ✂️  Срезаны *действия* из ответа")

    if "[SILENCE]" in reply:
        if _he_worries_you_are_gone(combined):
            log("   🔄 Модель хотела [SILENCE], но он беспокоится — "
                "прошу ответить заново")
            history_retry = history + [
                {"role": "assistant", "content": reply},
                {"role": "system",
                 "content": (
                     "🔴 ОН СПРАШИВАЕТ, ГДЕ ТЫ. Не молчи. "
                     "Ответь коротко: «тут я», «да здесь, закрутилась», "
                     "«на работе была». Без [SILENCE]."
                 )},
            ]
            reply = await asyncio.to_thread(ask_text, history_retry)
            reply = reply.replace("[SILENCE]", "").strip()
            if not reply.strip():
                reply = "Тут я, извини."
        else:
            log("   🤐 Модель решила промолчать.")
            add_silence_marker(user_id, max_history=MAX_HISTORY)
            unread.append(user_id, combined)

            async def _silent_processing():
                await asyncio.sleep(3)
                try:
                    await evaluate_and_update(
                        user_id, combined, "[промолчала]", user_display
                    )
                except Exception as e:
                    log(f"   ⚠️ silent combined_eval: {e}")
                try:
                    await wm_update(user_id, combined, "[промолчала]", user_display)
                except Exception as e:
                    log(f"   ⚠️ silent WM: {e}")
                try:
                    from lada.app import post_response
                    await post_response.run(user_id, combined, "")
                except Exception as e:
                    log(f"   ⚠️ silent post_response: {e}")

            asyncio.create_task(_silent_processing())
            return

    remember_matches = re.findall(
        r'\[REMEMBER\]\s*(.+?)(?=\n\[|\Z)', reply, re.DOTALL
    )
    if remember_matches:
        for fact in remember_matches:
            fact = fact.strip()
            if not fact or len(fact) < 5:
                continue
            ok = faiss_mem.add(
                fact,
                category="observation",
                source="explicit_remember",
                user_id=user_id,
                meta={
                    "valence": 0.0,
                    "mood_at_time": mood.get_mood(),
                    "trust_at_time": image_policy.get_trust(user_id),
                    "intimacy_at_time": image_policy.get_intimacy(user_id),
                    "top_sins": [],
                },
            )
            log(f"   💾 [REMEMBER] {'✓' if ok else '✗'}: {fact[:100]}")
        reply = re.sub(
            r'\[REMEMBER\]\s*.+?(?=\n\[|\Z)', '', reply, flags=re.DOTALL
        ).strip()
        if not reply.strip():
            reply = "Запомнила."

    propose_match = re.search(r'\[PROPOSE:\s*(lover|husband)\s*\]', reply)
    if propose_match and not get_pending(user_id) and get_status(user_id) not in rel.SPECIAL_STATUSES:
        which = propose_match.group(1)
        record_proposal(user_id, which)
        reply = reply.replace(propose_match.group(0), "").strip()
        log(f"   💗 Модель предложила отношения: {which}")

    night_match = re.search(r'\[PROPOSE:\s*night\s*\]', reply)
    if night_match and night_policy.can_ask_about_night(user_id):
        night_policy.record_night_ask(user_id)
        reply = reply.replace(night_match.group(0), "").strip()
        log(f"   🌙 Модель спросила разрешение писать ночью")

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

            low = image_hint.lower()
            mentions_cat = any(w in low for w in
                               ("cat", "kitten", "кошк", "кот", "мус"))
            mentions_lada = any(w in low for w in
                                ("girl", "woman", "self", "lada", "ladа"))
            if mentions_cat and not mentions_lada:
                image_subject = "pet"
                log(f"   🐱 Авто pet: hint упоминает кошку без Лады")

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
            refusal_scene = image_prompt
            log(f"   🚫 Отказ политики: {reason}")
            image_prompt = None
            if not reply.strip():
                reply = image_policy.explain_refusal(user_id, refusal_scene)

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
                log("   🤐 Fallback тоже пусто, молчу")
                add_silence_marker(user_id, max_history=MAX_HISTORY)
                unread.append(user_id, combined)

                async def _silent_processing_2():
                    await asyncio.sleep(3)
                    try:
                        await evaluate_and_update(
                            user_id, combined, "[промолчала]", user_display
                        )
                    except Exception as e:
                        log(f"   ⚠️ silent combined_eval: {e}")
                    try:
                        await wm_update(user_id, combined, "[промолчала]",
                                        user_display)
                    except Exception as e:
                        log(f"   ⚠️ silent WM: {e}")
                    try:
                        from lada.app import post_response
                        await post_response.run(user_id, combined, "")
                    except Exception as e:
                        log(f"   ⚠️ silent post_response: {e}")

                asyncio.create_task(_silent_processing_2())
                return

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
            unread.mark_read(user_id)
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
                add_message(
                    user_id, "system",
                    f"[СИСТЕМА] Ты отправила ему фото ({image_subject}). "
                    f"Это факт, не пиши такие пометки в своих ответах.",
                    max_history=MAX_HISTORY,
                )
                faiss_mem.add(
                    f"[Сгенерировала фото] {image_prompt}",
                    category="image",
                    source="image_gen",
                    user_id=user_id,
                    meta={
                        "valence": 0.0,
                        "mood_at_time": mood.get_mood(),
                        "trust_at_time": image_policy.get_trust(user_id),
                        "intimacy_at_time": image_policy.get_intimacy(user_id),
                        "top_sins": [],
                    },
                )
            except Exception as e:
                log(f"   ⚠️ Не удалось отправить изображение: {e}")
        else:
            log(f"   ⚠️ Генерация не вернула путь, пробую текстом")
            try:
                apology_history = history + [
                    {"role": "assistant", "content": reply},
                    {"role": "system",
                     "content": "Фото не получилось. Ответь текстом: коротко извинись "
                                "и предложи что-то другое."},
                ]
                fallback = await asyncio.to_thread(ask_text, apology_history)
                if fallback and fallback.strip():
                    await splitter.send_in_parts(client, chat_id, fallback)
            except Exception as e:
                log(f"   ⚠️ Fallback-текст не ушёл: {e}")

    if EMOTION_ENABLED:
        delay = 60 if image_prompt else 2

        async def _combined_later():
            await asyncio.sleep(delay)
            try:
                await evaluate_and_update(user_id, combined, reply, user_display)
            except Exception as e:
                log(f"   ⚠️ combined_eval: {e}")

            try:
                from lada.app import post_response
                await post_response.run(user_id, combined, reply)
            except Exception as e:
                log(f"   ⚠️ post_response: {e}")

        asyncio.create_task(_combined_later())


def _format_memory_line(r: dict) -> str:
    text = (r.get("text") or "").strip()
    cat = r.get("category", "?")
    v = r.get("valence")
    mood_at = r.get("mood_at_time")
    parts = [f"- [{cat}]"]
    if v is not None:
        try:
            parts.append(f"(v={float(v):+.2f}")
            if mood_at:
                parts.append(f" mood: {mood_at})")
            else:
                parts.append(")")
        except Exception:
            pass
    parts.append(text)
    return " ".join(parts)