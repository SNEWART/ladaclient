# initiative.py — Лада сама решает написать кому-то (SQLite + night policy)

import asyncio
import random
import re
from datetime import datetime
from functools import partial
from pathlib import Path

from config import (
    INITIATIVE_ENABLED,
    INITIATIVE_CHECK_INTERVAL_MINUTES, INITIATIVE_HOURS_SILENCE,
    INITIATIVE_CHANCE, INITIATIVE_TARGET, OWNER_ID,
    INITIATIVE_USER_COOLDOWN_HOURS, MAX_HISTORY,
)
from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.persona import get_persona
from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core.llm_json import chat_json
from lada.core.memory import add_message, get_history
from lada.core.working_memory import as_context_block as wm_block
from lada.core import users_names
from lada.brain import sins, mood, image_policy, image_mood, night_policy


INITIATIVE_LOG = Path("data") / "initiative.log"
INITIATIVE_LOG.parent.mkdir(exist_ok=True)

IMAGE_CHANCE = 0.28
INITIATIVE_SAFE_CLOTHING = "wearing hoodie or sweater, fully clothed, no nudity"

BANNED_TOPIC_WORDS = [
    "дожд", "пасмурн", "сыро", "льёт", "льет", "моросит", "луж",
    "погод",
]

TEMPLATE_PATTERNS = [
    r'^(привет|хай|ку|здарова|прив)\b',
    r'\bкак\s+(дела|ты|сам|сама)\b',
    r'\bчто\s+(нового|делаешь|делал|как|у тебя)\b',
    r'\bчем\s+(занят|занимаешься|занимался)\b',
    r'\bхорошего\s+дня\b',
    r'\bскучаю\s+по\s+тебе\b',
]


INITIATIVE_PROMPT = (
    "Ты — Лада. Ты сидишь одна, листаешь свою память и думаешь, "
    "не написать ли кому-нибудь. Скорее всего — не писать. Но если "
    "тебя что-то реально зацепило — напиши.\n\n"
    "Ниже: что ты помнишь об этом человеке, что вы обсуждали недавно.\n\n"
    "ТВОЯ ЗАДАЧА:\n"
    "Выбрать ОДНО конкретное воспоминание или фразу и написать об этом.\n"
    "Не «поболтать», а сказать что-то по существу.\n\n"
    "ФОРМАТ ОТВЕТА — строго JSON без markdown:\n"
    "{\n"
    '  "act": true или false,\n'
    '  "reason": "какое воспоминание зацепило и почему",\n'
    '  "message": "текст сообщения 1-3 предложения или пустая строка",\n'
    '  "image": "английское описание сцены или null"\n'
    "}\n\n"
    "ЖЁСТКИЕ ЗАПРЕТЫ:\n"
    "- НЕ писать «привет, как дела?», «что нового?», «чем занят?».\n"
    "- НЕ писать про дождь и погоду — никогда.\n"
    "- НЕ писать общее пожелание дня.\n"
    "- НЕ дублировать то, что уже было в последних сообщениях.\n"
    "- НЕ писать, если не можешь опереться на конкретное воспоминание.\n\n"
    "ХОРОШИЕ ПРИМЕРЫ:\n"
    "«Помнишь, ты говорил, что собираешься на горы? Уже выбрал место?»\n"
    "«Я тут вспомнила про твою кошку — она всё ещё таскает тапки?»\n"
    "«Думала про то, что ты рассказывал про работу. Как там проект?»\n\n"
    "ПЛОХИЕ ПРИМЕРЫ (НЕ ДЕЛАТЬ ТАК):\n"
    "«Привет, как ты?»\n"
    "«Что нового?»\n"
    "«Скучаю»\n"
    "«Хорошего дня!»\n\n"
    "Если НИ ОДНО воспоминание не цепляет — act=false, message пустое."
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def touch_user_activity(user_id: int):
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO user_activity (user_id, last_activity) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_activity = excluded.last_activity",
            (user_id, ts),
        )


def hours_since_last(user_id: int) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT last_activity FROM user_activity WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["last_activity"]:
        return 999.0
    try:
        dt = datetime.fromisoformat(row["last_activity"])
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def hours_since_last_initiative(user_id: int) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT last_initiative FROM user_initiative WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["last_initiative"]:
        return 999.0
    try:
        dt = datetime.fromisoformat(row["last_initiative"])
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def touch_initiative(user_id: int):
    ts = _now()
    with Transaction() as conn:
        ensure_user(user_id)
        conn.execute(
            "INSERT INTO user_initiative (user_id, last_initiative) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_initiative = excluded.last_initiative",
            (user_id, ts),
        )


def _should_reach_out(user_id: int) -> bool:
    since_init = hours_since_last_initiative(user_id)
    if since_init < INITIATIVE_USER_COOLDOWN_HOURS:
        return False

    silence = hours_since_last(user_id)
    if silence < INITIATIVE_HOURS_SILENCE:
        return False

    state = sins.get_state()
    m = mood.get_mood()

    if m in ("раздражённая", "расстроенная"):
        return False
    if state.get("гнев", 50) > 75:
        return False
    if state.get("гнев", 50) - state.get("терпение", 50) > 30:
        return False
    if state.get("уныние", 50) > 85 and state.get("усердие", 50) < 25:
        return False
    return True


def _is_banned_topic(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in BANNED_TOPIC_WORDS)


def _looks_template(text: str) -> bool:
    low = text.lower().strip()
    if len(low) > 120:
        return False
    for pat in TEMPLATE_PATTERNS:
        if re.search(pat, low):
            return True
    return False


def _collect_user_memories(user_id: int, n: int = 10) -> list:
    user_meta = [
        m for m in faiss_mem.metadata
        if m.get("user_id") == user_id
        and m.get("category") in ("observation", "emotion")
    ]
    user_meta = sorted(
        user_meta, key=lambda m: m.get("timestamp", ""), reverse=True
    )

    out = []
    seen = set()
    for m in user_meta:
        t = (m.get("text") or "").strip()
        if len(t) < 15 or len(t) > 300:
            continue
        if _is_banned_topic(t):
            continue
        key = t[:60].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": t, "category": m.get("category", "?")})
        if len(out) >= n:
            break
    return out


def _collect_general_memories(n: int = 5) -> list:
    meta = [
        m for m in faiss_mem.metadata
        if m.get("user_id") is None
        and m.get("category") in ("reflection",)
    ]
    meta = sorted(meta, key=lambda m: m.get("timestamp", ""), reverse=True)
    out = []
    for m in meta[:n]:
        t = (m.get("text") or "").strip()
        if 20 < len(t) < 400 and not _is_banned_topic(t):
            out.append({"text": t, "category": m.get("category", "?")})
    return out


def _build_context(user_id: int) -> str:
    from lada.brain import relationship
    name = users_names.display_name(user_id)

    parts = []
    parts.append(f"Пользователь: {name} (ID {user_id}).")
    parts.append(f"Прошло {hours_since_last(user_id):.1f} ч "
                 f"с последнего сообщения.")
    parts.append(f"Твоё настроение: {mood.get_mood()}.")
    parts.append(f"Статус отношений: "
                 f"{relationship.get_status_label(user_id)}.")

    wm = wm_block(user_id)
    if wm:
        parts.append("")
        parts.append(wm)

    user_mem = _collect_user_memories(user_id, n=10)
    if user_mem:
        parts.append("")
        parts.append("=== ЧТО ТЫ ПОМНИШЬ ОБ ЭТОМ ЧЕЛОВЕКЕ ===")
        for m in user_mem:
            parts.append(f"- [{m['category']}] {m['text']}")
    else:
        parts.append("")
        parts.append("(О нём ты пока почти ничего не знаешь.)")

    history = get_history(user_id, limit=10)
    if history:
        parts.append("")
        parts.append("=== ПОСЛЕДНИЕ СООБЩЕНИЯ (не повторяй их) ===")
        for m in history:
            role = "Он" if m["role"] == "user" else "Ты"
            content = (m.get("content") or "")[:160]
            parts.append(f"  {role}: {content}")

    general = _collect_general_memories(n=3)
    if general:
        parts.append("")
        parts.append("=== О ЧЁМ ТЫ ДУМАЛА ВООБЩЕ ===")
        for m in general:
            parts.append(f"- {m['text'][:200]}")

    parts.append("")
    parts.append(
        "НАПОМИНАНИЕ: выбери ОДНО конкретное воспоминание и напиши про него. "
        "Никаких «привет, как дела». Никакого дождя и погоды. "
        "Если не за что зацепиться — промолчи (act=false)."
    )

    return "\n".join(parts)


async def try_reach_out(client, user_id: int) -> bool:
    if not INITIATIVE_ENABLED:
        return False
    if not _should_reach_out(user_id):
        return False
    if random.random() > INITIATIVE_CHANCE:
        return False

    # ─── Ночное окно: пишем только с разрешения ───
    if night_policy.is_night_now():
        ok = night_policy.get_night_ok(user_id)
        if ok != 1:
            print(f"   🌙 Инициатива → {user_id}: ночь, "
                  f"разрешения нет (night_ok={ok})")
            return False

    context = _build_context(user_id)
    messages = [
        {"role": "system", "content": get_persona() + "\n\n" + INITIATIVE_PROMPT},
        {"role": "user", "content": context},
    ]

    data = await chat_json(messages, "initiative")
    if not isinstance(data, dict):
        return False

    act = bool(data.get("act"))
    message = (data.get("message") or "").strip()
    reason = (data.get("reason") or "").strip()
    image_scene = data.get("image")

    if not act or not message or len(message) < 5:
        print(f"   💤 Инициатива → {user_id}: промолчала ({reason[:80]})")
        return False

    if _is_banned_topic(message):
        print(f"   🚫 Инициатива отменена: дождь/погода")
        return False

    if _looks_template(message):
        print(f"   🚫 Инициатива отменена: похоже на шаблон")
        return False

    image_prompt = None
    if image_scene and isinstance(image_scene, str) \
            and image_scene.strip().lower() != "null":
        if random.random() < IMAGE_CHANCE:
            scene = image_scene.strip()[:200]
            print(f"   🔍 Сцена: {scene[:120]}")

            allowed, reason_msg = image_policy.check_allowed(
                user_id, scene, source="initiative"
            )
            if allowed:
                image_prompt = scene + ", " + INITIATIVE_SAFE_CLOTHING
                print(f"   ✅ Фото разрешено: {reason_msg}")
            else:
                print(f"   🚫 Фото отклонено: {reason_msg}")

    try:
        await client.send_message(user_id, message)
    except Exception as e:
        print(f"⚠️ Не удалось отправить инициативу: {e}")
        return False

    try:
        add_message(user_id, "assistant", message, max_history=MAX_HISTORY)
    except Exception as e:
        print(f"⚠️ Не удалось записать инициативу в историю: {e}")

    touch_user_activity(user_id)
    touch_initiative(user_id)
    faiss_mem.add(message, category="initiative",
                  source="autonomous", user_id=user_id)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(INITIATIVE_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n[{ts}] → {user_id}\n{message}\nПричина: {reason}\n")
        if image_prompt:
            f.write(f"Фото: {image_prompt}\n")

    print(f"   💌 Инициатива → {user_id}: {message[:80]}")

    if image_prompt:
        try:
            from lada.tools import image_gen

            extra_neg = image_mood.get_negative_for_mood(
                mood.get_mood(),
                force_level=3,
                for_initiative=True,
            )
            neg = image_gen.BASE_NEGATIVE + ", " + extra_neg

            img_path = await asyncio.to_thread(
                partial(
                    image_gen.generate,
                    image_prompt,
                    negative_prompt=neg,
                    subject="lada",
                )
            )

            if img_path:
                await client.send_file(user_id, img_path)
                print(f"   🖼 Фото отправлено")
                try:
                    add_message(
                        user_id, "system",
                        f"[СИСТЕМА] Ты только что отправила ему фото: "
                        f"{image_prompt[:80]}. Это факт из прошлого, "
                        f"НЕ пиши такие пометки в своих сообщениях.",
                        max_history=MAX_HISTORY,
                    )
                except Exception:
                    pass
                faiss_mem.add(
                    f"[Сама прислала фото] {image_prompt}",
                    category="image",
                    source="initiative",
                    user_id=user_id,
                )
        except Exception as e:
            print(f"   ⚠️ Ошибка генерации: {e}")

    return True


async def initiative_loop(client):
    if not INITIATIVE_ENABLED:
        return
    await asyncio.sleep(300)
    while True:
        try:
            targets = []
            if INITIATIVE_TARGET == "owner":
                targets = [OWNER_ID]
            else:
                from lada.core.memory import list_users
                targets = list_users()

            for uid in targets:
                await try_reach_out(client, uid)
        except Exception as e:
            print(f"⚠️ Ошибка в цикле инициативы: {e}")

        await asyncio.sleep(INITIATIVE_CHECK_INTERVAL_MINUTES * 60)