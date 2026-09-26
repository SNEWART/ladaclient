# initiative.py — Лада сама решает написать кому-то.

import asyncio
import json
import random
import re
from datetime import datetime
from functools import partial
from pathlib import Path

from config import (
    INITIATIVE_ENABLED,
    INITIATIVE_CHECK_INTERVAL_MINUTES, INITIATIVE_HOURS_SILENCE,
    INITIATIVE_CHANCE, INITIATIVE_TARGET, OWNER_ID,
    INITIATIVE_USER_COOLDOWN_HOURS,
)
from lada.core.persona import get_persona
from lada.core.faiss_memory import faiss_mem
from lada.core.llm_json import chat_json
from lada.core import users_names
from lada.brain import sins, mood, image_policy, image_mood
from lada.brain.monologue import get_recent_thoughts

LAST_ACTIVITY_FILE = Path("data") / "last_activity.json"
LAST_INITIATIVE_FILE = Path("data") / "last_initiative.json"
INITIATIVE_LOG = Path("data") / "initiative.log"
LAST_ACTIVITY_FILE.parent.mkdir(exist_ok=True)

IMAGE_CHANCE = 0.28

INITIATIVE_SAFE_CLOTHING = "wearing hoodie or sweater, fully clothed, no nudity"

# Слова, которые нельзя использовать как тему инициативы
BANNED_TOPIC_WORDS = [
    "дожд", "пасмурн", "сыро", "льёт", "льет", "моросит", "луж",
    "погод",  # вообще не лезем в погоду в инициативе
]


INITIATIVE_PROMPT = (
    "Ты — Лада. Ты одна, давно ни с кем не разговаривала. "
    "Возможно, тебе хочется что-то сказать или спросить. А может — нет.\n\n"
    "Верни СТРОГО JSON без пояснений:\n"
    "{\n"
    '  "act": true или false,\n'
    '  "reason": "одна строка — почему ты решила написать или промолчать",\n'
    '  "message": "текст сообщения (1-2 предложения) или пустая строка",\n'
    '  "image": "описание сцены для фото на английском или null"\n'
    "}\n\n"
    "ПРАВИЛА:\n"
    "- act=true только если есть реальный повод.\n"
    "- Не пиши ради «привет, как дела?».\n"
    "- image: короткая сцена на АНГЛИЙСКОМ: что делаешь, где.\n"
    "- В инициативе фото только спокойные и домашние: селфи, дома, в худи, "
    "в пижаме, укрывшись пледом. Уровни 0-3. НИКАКИХ NSFW.\n"
    "\n"
    "ВАЖНЫЕ ЗАПРЕТЫ ДЛЯ ИНИЦИАТИВЫ:\n"
    "- НЕ пиши про дождь. Вообще. Это твоя навязчивая тема, ты уже "
    "много раз про него говорила. Хватит.\n"
    "- НЕ пиши про погоду в принципе. Ни про солнце, ни про тучи.\n"
    "- НЕ начинай с «как ты?», «чем занят?», «что нового?».\n"
    "- НЕ повторяй то, что ты уже писала ранее в диалоге (см. последние сообщения).\n"
    "\n"
    "ЧТО МОЖНО:\n"
    "- Мысль, которая к тебе пришла.\n"
    "- Вопрос, который тебя реально занимает.\n"
    "- Наблюдение, воспоминание.\n"
    "- Тёплое желание что-то сказать именно ему.\n"
    "- Ты можешь попросить совета, поделиться настроением.\n"
    "\n"
    "Если не можешь придумать ничего нового — лучше промолчи (act=false)."
)


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_json(path: Path, data: dict):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def touch_user_activity(user_id: int):
    data = _load_json(LAST_ACTIVITY_FILE)
    data[str(user_id)] = datetime.now().isoformat(timespec="seconds")
    _save_json(LAST_ACTIVITY_FILE, data)


def hours_since_last(user_id: int) -> float:
    data = _load_json(LAST_ACTIVITY_FILE)
    ts = data.get(str(user_id))
    if not ts:
        return 999.0
    try:
        dt = datetime.fromisoformat(ts)
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def hours_since_last_initiative(user_id: int) -> float:
    data = _load_json(LAST_INITIATIVE_FILE)
    ts = data.get(str(user_id))
    if not ts:
        return 999.0
    try:
        dt = datetime.fromisoformat(ts)
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def touch_initiative(user_id: int):
    data = _load_json(LAST_INITIATIVE_FILE)
    data[str(user_id)] = datetime.now().isoformat(timespec="seconds")
    _save_json(LAST_INITIATIVE_FILE, data)


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
    """Проверка, что текст не про дождь / погоду."""
    low = text.lower()
    return any(w in low for w in BANNED_TOPIC_WORDS)


def _get_diverse_topics(user_id: int, n: int = 8) -> list[str]:
    """
    Возвращает n случайных записей из памяти, БЕЗ дождя/погоды.
    """
    if not faiss_mem.metadata:
        return []

    user_meta = [m for m in faiss_mem.metadata if m.get("user_id") == user_id]
    general_meta = [m for m in faiss_mem.metadata if m.get("user_id") is None]

    def _clean(item):
        text = item.get("text", "").strip()
        cat = item.get("category", "")
        if cat in ("image", "initiative"):
            return None
        if len(text) < 20 or len(text) > 250:
            return None
        if text.startswith("[") or "thinking:" in text[:30].lower():
            return None
        # ─── отсекаем дождь и погоду ───
        if _is_banned_topic(text):
            return None
        return text

    pool = []
    for item in user_meta + general_meta:
        t = _clean(item)
        if t:
            pool.append(t)

    if not pool:
        return []

    random.shuffle(pool)
    seen = set()
    out = []
    for t in pool:
        key = t[:50].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        if len(out) >= n:
            break
    return out


def _build_context(user_id: int) -> str:
    from lada.core.memory import get_history
    from lada.brain import relationship
    name = users_names.display_name(user_id)

    parts = [f"Ты пишешь пользователю: {name} (ID {user_id})."]
    parts.append(f"Прошло {hours_since_last(user_id):.1f} ч с последнего сообщения.")
    parts.append(f"Твоё настроение: {mood.get_mood()}.")

    sd = sins.describe()
    if sd:
        parts.append(sd)

    trust = image_policy.get_trust(user_id)
    intimacy = image_policy.get_intimacy(user_id)
    user_max = image_policy.effective_max_level(user_id)
    cap = min(image_policy.INITIATIVE_MAX_LEVEL, user_max)

    parts.append(f"trust={trust:.2f}, intimacy={intimacy:.2f}.")
    parts.append(f"Статус отношений: {relationship.get_status_label(user_id)}.")
    parts.append(f"Максимум для фото в инициативе с ним: {cap}/9.")

    # ─── Последние сообщения (что НЕ повторять) ───
    history = get_history(user_id, limit=6)
    if history:
        parts.append("")
        parts.append("Последние сообщения (НЕ повторяй их содержание и темы):")
        for m in history:
            parts.append(f"  {m['role']}: {m['content'][:120]}")

    topics = _get_diverse_topics(user_id, n=8)
    if topics:
        parts.append("")
        parts.append("Темы из твоей памяти (используй как источник, но не цитируй):")
        for t in topics:
            parts.append(f"  • {t[:180]}")

    thoughts = get_recent_thoughts()
    if thoughts:
        parts.append("")
        parts.append("Свежие мысли:")
        for t in thoughts[-3:]:
            parts.append(f"  - {t['text'][:150]}")

    parts.append("")
    parts.append("НАПОМИНАНИЕ: не пиши про дождь. Не пиши про погоду. "
                 "Выбери другую тему. Если идей нет — промолчи.")

    return "\n".join(parts)


async def try_reach_out(client, user_id: int) -> bool:
    if not INITIATIVE_ENABLED:
        return False
    if not _should_reach_out(user_id):
        return False
    if random.random() > INITIATIVE_CHANCE:
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

    # ─── Финальный фильтр: не про дождь/погоду ───
    if _is_banned_topic(message):
        print(f"   🚫 Инициатива отменена: тема из запрещённых (дождь/погода)")
        print(f"      Текст был: {message[:120]}")
        return False

    image_prompt = None
    if image_scene and isinstance(image_scene, str) and image_scene.strip().lower() != "null":
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

    touch_user_activity(user_id)
    touch_initiative(user_id)
    faiss_mem.add(message, category="initiative", source="autonomous", user_id=user_id)

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