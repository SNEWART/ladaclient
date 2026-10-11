# commands.py — команды владельца (всё, что начинается с /)

import asyncio

from lada.app.client import message_queue, get_client
from config import OWNER_ID
from lada.core import users_names, user_city
from lada.core.memory import list_users
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.telegram.mode import get_mode, set_mode
from lada.brain import mood, sins, image_policy, activity
from lada.brain.relationship import (
    familiarity, get_status, get_status_label, get_global_cap,
    get_pending, set_status,
)
from lada.brain.sleep import consolidate
from lada.brain.monologue import think_once
from lada.tools import weather, image_gen


async def handle_owner_command(text: str, event) -> bool:
    text = text.strip()
    owner_id = OWNER_ID

    if text.startswith("/mode"):
        parts = text.split()
        if len(parts) == 2 and parts[1] in ("all", "owner"):
            set_mode(parts[1])
            await event.respond(f"✅ Режим: **{parts[1]}**")
        else:
            current = get_mode()
            await event.respond(f"Текущий режим: **{current}**\n"
                                f"Использование: `/mode all` или `/mode owner`.")
        return True

    # 🎭 Занятие — тест и управление
    if text.startswith("/activity"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            cur = activity.get_current()
            if cur:
                await event.respond(
                    f"🎭 Сейчас Лада: _{cur.get('text', '—')}_\n"
                    f"Период: {cur.get('period', '?')}\n"
                    f"С: {cur.get('since', '?')}\n\n"
                    f"Сменить: `/activity <текст>`\n"
                    f"Случайно: `/activity roll`"
                )
            else:
                await event.respond(
                    "🎭 Занятие не установлено.\n"
                    "Поставить: `/activity <текст>`\n"
                    "Случайно: `/activity roll`"
                )
        else:
            arg = parts[1].strip()
            if arg == "roll":
                new_text = activity.pick_new()
                activity.set_current(new_text)
                await event.respond(f"🎲 Новое занятие: _{new_text}_")
            else:
                activity.set_current(arg)
                await event.respond(f"✅ Занятие: _{arg}_")
        return True

    if text.startswith("/city"):
        parts = text.split(maxsplit=2)
        if len(parts) == 1:
            my = user_city.get_city(owner_id)
            await event.respond(
                f"Твой город: **{my or 'не задан'}**\n"
                f"Использование:\n"
                f"`/city <город>` — задать себе\n"
                f"`/city <user_id> <город>` — задать другому"
            )
        elif len(parts) == 2:
            city = parts[1]
            user_city.set_city(owner_id, city)
            w = weather.get_weather(city, force=True)
            await event.respond(
                f"✅ Твой город: **{city}**\n"
                f"Погода: {w or 'не удалось получить'}"
            )
        else:
            try:
                target = int(parts[1])
                city = parts[2]
                user_city.set_city(target, city)
                w = weather.get_weather(city, force=True)
                name = users_names.display_name(target)
                await event.respond(
                    f"✅ Город для **{name}** (ID {target}): **{city}**\n"
                    f"Погода: {w or 'не удалось получить'}"
                )
            except ValueError:
                await event.respond("Ошибка: ID должен быть числом")
        return True

    if text.startswith("/nocite"):
        parts = text.split()
        if len(parts) == 1:
            user_city.set_city(owner_id, None)
            await event.respond("✅ Твой город сброшен")
        elif len(parts) == 2 and parts[1].lstrip("-").isdigit():
            user_city.set_city(int(parts[1]), None)
            await event.respond(f"✅ Город для ID {parts[1]} сброшен")
        else:
            await event.respond("Использование: `/nocite [user_id]`")
        return True

    if text.startswith("/weather"):
        parts = text.split()
        target = owner_id
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
        city = user_city.get_city(target)
        if not city:
            await event.respond(f"Город для ID {target} не задан.")
            return True
        w = weather.get_weather(city, force=True)
        name = users_names.display_name(target)
        if w:
            await event.respond(f"🌤 **Погода** для {name} ({city}):\n{w}")
        else:
            await event.respond(f"⚠️ Не удалось получить погоду для {city}")
        return True

    if text == "/reflect":
        from lada.brain.reflection import reflect
        await event.respond("🧠 Запускаю рефлексию...")
        await reflect()
        await event.respond("✅ Рефлексия записана.")
        return True

    if text == "/sleep":
        await event.respond("💤 Запускаю консолидацию...")
        result = await consolidate()
        await event.respond(f"💤 {result}")
        return True

    if text == "/think":
        await event.respond("💭 Думаю...")
        thought = await think_once()
        await event.respond(f"💭 _{thought}_" if thought else "Пусто.")
        return True

    if text == "/thoughts":
        recent = [m for m in faiss_mem.metadata if m.get("category") == "monologue"][-10:]
        if not recent:
            await event.respond("📭 Мыслей пока нет.")
        else:
            lines = ["💭 **Последние мысли:**"]
            for it in recent:
                ts = it.get("timestamp", "")[:16].replace("T", " ")
                lines.append(f"_{ts}_ — {it['text'][:200]}")
            await event.respond("\n\n".join(lines))
        return True

    if text == "/clear_thoughts":
        from lada.brain.monologue import clear_all as clear_monologues
        clear_monologues()
        await event.respond("🗑 Все мысли стёрты.")
        return True

    if text == "/soul":
        state = sins.get_state()
        lines = ["⚖️ **Глубинное состояние:**"]
        for k, v in state.items():
            bar = "█" * int(v / 10) + "░" * (10 - int(v / 10))
            lines.append(f"`{bar}` {k}: {int(v)}")
        lines.append(f"\n💭 Настроение: **{mood.get_mood()}**")
        await event.respond("\n".join(lines))
        return True

    if text == "/whoami":
        from lada.brain.relationship import familiarity_label
        f = familiarity_label(owner_id)
        await event.respond(f"Ты для меня — **{f}**.")
        return True

    if text == "/observations":
        items = [
            m for m in faiss_mem.metadata
            if m.get("category") == "observation" and m.get("user_id") == owner_id
        ][-20:]
        if not items:
            await event.respond("👁 Наблюдений пока нет.")
        else:
            lines = ["👁 **Что я заметила о тебе:**"]
            for it in items:
                lines.append(f"• {it['text'][:200]}")
            await event.respond("\n".join(lines))
        return True

    if text == "/queue":
        await event.respond(f"📬 В очереди: {message_queue.size()}")
        return True

    if text == "/users":
        ids = list_users()
        if not ids:
            await event.respond("📭 Пользователей пока нет.")
        else:
            lines = ["👥 **Пользователи в памяти:**"]
            for uid in ids:
                name = users_names.display_name(uid)
                city = user_city.get_city(uid)
                city_str = f" · {city}" if city else ""
                if name.startswith("ID "):
                    lines.append(f"• {name}{city_str}")
                else:
                    lines.append(f"• {name} — ID {uid}{city_str}")
            await event.respond("\n".join(lines))
        return True

    if text.startswith("/divorce"):
        parts = text.split()
        target = owner_id
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
        name = users_names.display_name(target)
        current_status = get_status(target)
        current_intimacy = image_policy.get_intimacy(target)

        set_status(target, "neutral", note="manual divorce by owner")
        image_policy.set_trust(target, intimacy=0.5)

        await event.respond(
            f"💔 Развод оформлен.\n"
            f"👤 {name} (ID {target})\n"
            f"   статус: {current_status} → neutral\n"
            f"   intimacy: {current_intimacy:.2f} → 0.50\n"
            f"   trust не тронут"
        )
        return True

    if text.startswith("/status"):
        parts = text.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
            name = users_names.display_name(target)
            st = get_status(target)
            pending = get_pending(target)
            pending_str = f" (pending: {pending})" if pending else ""
            await event.respond(
                f"👤 {name} (ID {target}) — статус: **{get_status_label(target)}** "
                f"(`{st}`){pending_str}"
            )
        elif len(parts) == 3 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
            new_status = parts[2].lower()
            if new_status in ("neutral", "acquaintance", "lover", "husband"):
                set_status(target, new_status, note="manual by owner")
                await event.respond(f"✅ Статус для {target}: **{get_status_label(target)}**")
            else:
                await event.respond("Статус: neutral | acquaintance | lover | husband")
        else:
            await event.respond(
                "Использование:\n"
                "`/status <user_id>` — показать\n"
                "`/status <user_id> <neutral|acquaintance|lover|husband>` — задать\n"
                "`/divorce [user_id]` — снять статус и снизить intimacy до 0.5"
            )
        return True

    if text.startswith("/trust"):
        parts = text.split()
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
            if len(parts) == 2:
                t = image_policy.get_trust(target)
                i = image_policy.get_intimacy(target)
                f = familiarity(target)
                lvl = image_policy._max_level_for_user(target)
                eff = image_policy.effective_max_level(target)
                ovr = image_policy.get_max_level_override(target)
                name = users_names.display_name(target)
                st = get_status_label(target)
                ovr_str = f"\n   override: {ovr}/9" if ovr is not None else ""
                cap = get_global_cap(target)
                cap_str = f"\n   global_cap: {cap}/9" if cap is not None else ""
                await event.respond(
                    f"👤 {name} (ID {target})\n"
                    f"   статус: {st}\n"
                    f"   familiarity: {f:.2f}\n"
                    f"   trust: {t:.2f}\n"
                    f"   intimacy: {i:.2f}\n"
                    f"   базовый уровень: {lvl}/9\n"
                    f"   эффективный: {eff}/9{ovr_str}{cap_str}"
                )
            elif len(parts) >= 3:
                try:
                    t = float(parts[2])
                    i = float(parts[3]) if len(parts) >= 4 else None
                    image_policy.set_trust(target, trust=t, intimacy=i)
                    await event.respond(f"✅ Обновлено для ID {target}")
                except ValueError:
                    await event.respond("Ошибка: числа не распознаны")
        else:
            await event.respond(
                "Использование:\n"
                "`/trust <user_id>` — показать\n"
                "`/trust <user_id> <trust> [intimacy]` — задать (0..1)\n"
                "`/level <user_id> <0..9>` — оверрайд макс. уровня\n"
                "`/level <user_id> auto` — снять оверрайд\n"
                "`/status <user_id>` — статус\n"
                "`/divorce [user_id]` — развод\n"
                "`/city [user_id] <город>` — город для погоды"
            )
        return True

    if text.startswith("/level"):
        parts = text.split()
        if len(parts) == 3 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
            val = parts[2].lower()
            if val == "auto":
                image_policy.set_max_level_override(target, None)
                await event.respond(f"✅ Оверрайд снят для ID {target}")
            elif val.isdigit() and 0 <= int(val) <= 9:
                image_policy.set_max_level_override(target, int(val))
                await event.respond(f"✅ Максимум для ID {target}: {val}/9")
            else:
                await event.respond("Значение: 0..9 или auto")
        else:
            await event.respond(
                "Использование:\n"
                "`/level <user_id> <0..9>`\n"
                "`/level <user_id> auto`"
            )
        return True

    if text == "/trusts":
        ids = list_users()
        if not ids:
            await event.respond("📭 Пользователей нет.")
        else:
            lines = ["🔐 **Уровни доверия:**"]
            for uid in ids:
                name = users_names.display_name(uid)
                t = image_policy.get_trust(uid)
                i = image_policy.get_intimacy(uid)
                f = familiarity(uid)
                lvl = image_policy._max_level_for_user(uid)
                eff = image_policy.effective_max_level(uid)
                ovr = image_policy.get_max_level_override(uid)
                st = get_status_label(uid)
                city = user_city.get_city(uid)
                city_str = f", city={city}" if city else ""
                ovr_str = f", override={ovr}" if ovr is not None else ""
                lines.append(f"• [{st}] {name}: trust={t:.2f}, int={i:.2f}, "
                             f"fam={f:.2f}, base={lvl}, eff={eff}"
                             f"{ovr_str}{city_str}")
            await event.respond("\n".join(lines))
        return True

    if text.startswith("/image"):
        prompt = text[len("/image"):].strip()
        if not prompt:
            await event.respond("Использование: `/image описание сцены`")
            return True

        allowed, reason = image_policy.check_allowed(owner_id, prompt, source="chat")
        if not allowed:
            await event.respond(f"🚫 Отказано: {reason}")
            return True

        await event.respond(f"🎨 Генерирую: _{prompt}_")
        img_path = await asyncio.to_thread(image_gen.generate, prompt)
        if img_path:
            try:
                client = get_client()
                await client.send_file(event.chat_id, img_path)
            except Exception as e:
                await event.respond(f"⚠️ Не удалось отправить: {e}")
        else:
            await event.respond("⚠️ Не удалось сгенерировать.")
        return True

    if text == "/stats":
        lines = [
            f"🧠 FAISS всего: {faiss_mem.count()}",
            f"   👁 наблюдения (о тебе): {faiss_mem.count_by_category('observation', owner_id)}",
            f"   💭 эмоции: {faiss_mem.count_by_category('emotion', owner_id)}",
            f"   🗣 монологи: {faiss_mem.count_by_category('monologue', None)}",
            f"   🪞 рефлексии: {faiss_mem.count_by_category('reflection', None)}",
            f"💭 Настроение: {mood.get_mood()}",
            f"🎭 Занятие: {activity.get_current_text() or '—'}",
            f"📬 Очередь: {message_queue.size()}",
        ]
        await event.respond("\n".join(lines))
        return True

    return False