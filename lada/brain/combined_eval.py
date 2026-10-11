# lada/brain/combined_eval.py — слияние emotions и WM в один LLM-вызов.
# Обогащено: valence, meta (mood_at_time, trust_at_time, ...).
#
# FIX: _top_sins использовал неопределённую v — модуль падал целиком.
# FIX: программный фильтр observations — нормализует префиксы
#      («Пользователь сказал, что X» → «X») и режет мусор,
#      включая атрибуты Лады (Муся и пр.), приписанные пользователю.

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core.llm_json import chat_json
from lada.core.working_memory import load as wm_load, save as wm_save, MAX_CHARS
from lada.brain import sins, mood, image_policy, relationship
from config import (
    EMOTION_ENABLED,
    MOOD_NEGATIVE_MULTIPLIER, MOOD_POSITIVE_MULTIPLIER,
)

EMOTIONAL_DIARY = Path("data") / "emotional_diary.log"
EMOTIONAL_DIARY.parent.mkdir(exist_ok=True)


COMBINED_PROMPT = (
    "Ты — внутренний аналитик диалога Лады. По одному диалогу верни "
    "СТРОГО JSON без markdown:\n"
    "{\n"
    '  "emotion": "краткая эмоция Лады, 1 предложение, или SKIP",\n'
    '  "emotion_valence": 0.0,\n'
    '  "mood": "спокойная | весёлая | задумчивая | раздражённая | уставшая | игривая | холодная | тёплая | расстроенная | вдохновлённая | нейтральная",\n'
    '  "sins_deltas": {\n'
    '    "гордыня": 0, "смирение": 0,\n'
    '    "жадность": 0, "щедрость": 0,\n'
    '    "похоть": 0, "целомудрие": 0,\n'
    '    "зависть": 0, "доброжелательность": 0,\n'
    '    "чревоугодие": 0, "умеренность": 0,\n'
    '    "гнев": 0, "терпение": 0,\n'
    '    "уныние": 0, "усердие": 0\n'
    "  },\n"
    '  "trust_delta": 0.0,\n'
    '  "intimacy_delta": 0.0,\n'
    '  "observations": [\n'
    '    {"text": "факт о пользователе", "valence": 0.0}\n'
    "  ],\n"
    '  "wm_update": "полный новый текст рабочей памяти для этого человека (markdown)"\n'
    "}\n\n"
    "ПРАВИЛА:\n"
    "- Числа в sins_deltas: -3..3, обычно -1/0/1.\n"
    "- trust_delta: -0.05..+0.05. Не превышай.\n"
    "- intimacy_delta: -0.05..+0.05. Не превышай.\n"
    "- emotion_valence: число от -1.0 до +1.0. Окраска этой эмоции.\n"
    "- emotion: если диалог живой — опиши. Если технический — SKIP.\n"
    "\n"
    "🔴 OBSERVATIONS — САМОЕ ВАЖНОЕ ПРАВИЛО:\n"
    "\n"
    "Пиши БЕЗ префикса «Пользователь сказал/спросил/написал». "
    "Сразу сам факт:\n"
    "  ПРАВИЛЬНО: «Работает программистом»\n"
    "  ПРАВИЛЬНО: «Живёт в Санкт-Петербурге»\n"
    "  ПРАВИЛЬНО: «Любит горы»\n"
    "  ПРАВИЛЬНО: «Учится в РГТУ»\n"
    "  НЕПРАВИЛЬНО: «Пользователь сказал, что…»\n"
    "  НЕПРАВИЛЬНО: «Пользователь спросил…»\n"
    "  НЕПРАВИЛЬНО: «Он написал мне привет»\n"
    "\n"
    "Это ПОСТОЯННЫЕ факты — то, что остаётся правдой через неделю: "
    "работа, город, хобби, вкусы, характер, семья, привычки.\n"
    "\n"
    "🔴 МУСЯ — ЭТО КОШКА ЛАДЫ. Она НЕ принадлежит пользователю. "
    "Никогда не пиши «у него есть кошка Муся». Это ошибка.\n"
    "\n"
    "НЕ наблюдения (вообще не пиши):\n"
    "  - Пересказ реплик («он сказал X», «он спросил Y»)\n"
    "  - Сиюминутные состояния («он устал сегодня», «он рад»)\n"
    "  - Всё про Ладу («он проявляет заботу о Ладе», "
    "«он считает Ладу обманщицей», «он пожелал Ладе спокойной ночи»)\n"
    "  - Общие рассуждения о людях («пользователи демонстрируют...»)\n"
    "  - Атрибуты Лады, приписанные пользователю (Муся, её кошка)\n"
    "\n"
    "Если в диалоге не было нового ПОСТОЯННОГО факта — пустой массив.\n"
    "\n"
    "valence каждой observation: окраска факта.\n"
    "  Хорошее о нём → +0.3..+0.8\n"
    "  Плохое / грустное → -0.3..-0.8\n"
    "  Нейтральный факт (город, работа, погода) → 0.0\n"
    "\n"
    "- wm_update: обнови рабочую память ТОЛЬКО об этом человеке. "
    "Формат: ОДИН заголовок «### ID <user_id> (<имя>)», под ним "
    "пункты через «- ». Максимум 20 строк.\n"
    "\n"
    "🔴 ВАЖНО ПРО МОЛЧАНИЕ ЛАДЫ:\n"
    "Если в диалоге вместо ответа Лады стоит «[промолчала]» — это "
    "значит, что она РЕШИЛА не отвечать. При оценке такого диалога:\n"
    "- НЕ накручивай негатив. Не увеличивай гнев, раздражение, уныние.\n"
    "- Она уже «выпустила пар» молчанием. Если была раздражена — "
    "СНИЖАЙ раздражение (гнев -1, терпение +1).\n"
    "- Если промолчала от усталости — оставь уставшую, не усиливай.\n"
    "- Если промолчала несколько раз подряд — тем более сдвигай "
    "к нейтральному или лёгкому позитиву.\n"
    "- mood при «[промолчала]» ставь чаще «спокойная» или "
    "«нейтральная», а не раздражённая.\n"
    "- ВСЁ В ОДНОМ JSON. Ничего лишнего."
)


def _apply_asymmetric_multiplier(deltas: dict) -> dict:
    out = {}
    for k, v in deltas.items():
        try:
            dv = float(v)
        except (TypeError, ValueError):
            out[k] = v
            continue
        if dv < 0:
            out[k] = dv * MOOD_NEGATIVE_MULTIPLIER
        else:
            out[k] = dv * MOOD_POSITIVE_MULTIPLIER
    return out


def _is_low_quality_wm(text: str) -> bool:
    if not text or len(text) < 20:
        return True
    low = text.lower().strip()
    if low in ("без изменений", "unchanged", "(пусто)", "пусто"):
        return True
    return False


def _top_sins(state: dict, n: int = 3) -> list:
    """Топ-n шкал по отклонению от нейтрали, по модулю."""
    devs = [(k, v - 50.0) for k, v in state.items()
            if abs(v - 50.0) > 5]
    devs.sort(key=lambda x: abs(x[1]), reverse=True)
    return [
        {"name": k, "value": round(d + 50.0, 1), "delta": round(d, 1)}
        for k, d in devs[:n]
    ]


# ─── НОРМАЛИЗАЦИЯ И ФИЛЬТР OBSERVATIONS ───

_OBS_MAX_LEN = 160

# Префиксы-пересказ, которые вырезаем, сохраняя суть.
_STRIP_PREFIXES = (
    r'^(?:пользователь|он|собеседник)\s+(?:сказал|написал|сообщил|'
    r'упомянул|рассказал|ответил|поделился|добавил|уточнил),?\s+'
    r'(?:что\s+)?',
    r'^(?:пользователь|он|собеседник)\s+(?:сообщил|упомянул)\s+о\s+',
    r'^у\s+(?:пользователя|него|собеседника)\s+',
)

# Жёсткий мусор — реплики и взаимодействия с Ладой.
_HARD_REJECT = (
    r'^(?:пользователь|он|собеседник)\s+(?:спросил|поинтересовался|'
    r'задал|пожелал|попросил\s+у|выразил|отреагировал|эмоционально)',
    r'^(?:пользователь|он)\s+написал\s+(?:мне|ей|ладе)',
    r'^(?:пользователь|он)\s+проявляет\s+(?:интерес\s+к\s+ладе|'
    r'заботу\s+о\s+ладе|внимание\s+к\s+ладе)',
    r'^пользователи\s+',
    r'^в\s+(?:этом\s+)?диалоге',
)

# Мусорные паттерны.
_OBS_BAD_PATTERNS = (
    re.compile(r'\b(?:о|про|к|с)\s+лад[оеыу]\b', re.IGNORECASE),
    re.compile(r'\bладе\b|\bладу\b|\bладой\b', re.IGNORECASE),
    re.compile(r'\bне\s+мешает\s+ли\b', re.IGNORECASE),
    re.compile(r'\bсейчас\b.*\b(?:устал|рад|груст|злит|бесит|споко)',
               re.IGNORECASE),
    # Атрибуты Лады, приписанные пользователю.
    re.compile(r'\bмус[яюией]', re.IGNORECASE),
    re.compile(r'\bкошк[аиу]?\s+лад', re.IGNORECASE),
)


def _normalize_observation(text: str) -> str:
    t = text.strip()
    for pat in _STRIP_PREFIXES:
        new = re.sub(pat, '', t, count=1, flags=re.IGNORECASE)
        if new != t:
            t = new.strip()
            if t and t[0].islower():
                t = t[0].upper() + t[1:]
            break
    return t


def _is_valid_observation(text: str) -> tuple:
    """(валидно, нормализованный_текст, причина_отказа)"""
    raw = (text or "").strip()
    if not raw:
        return False, raw, "пусто"

    low = raw.lower()

    for pat in _HARD_REJECT:
        if re.search(pat, low):
            return False, raw, f"мусор: {pat[:35]}"

    norm = _normalize_observation(raw)

    if len(norm) < 10:
        return False, raw, "коротко"
    if len(norm) > _OBS_MAX_LEN:
        return False, raw, f"длинно ({len(norm)})"

    for pat in _OBS_BAD_PATTERNS:
        if pat.search(norm):
            return False, raw, f"паттерн: {pat.pattern[:30]}"

    if not re.search(r'[а-яёa-z]{4,}', norm.lower()):
        return False, raw, "нет содержательных слов"

    return True, norm, ""


def _make_meta(user_id: int, valence,
               state_before: dict) -> dict:
    return {
        "valence": (None if valence is None
                    else max(-1.0, min(1.0, float(valence)))),
        "mood_at_time": state_before["mood"],
        "trust_at_time": round(state_before["trust"], 3),
        "intimacy_at_time": round(state_before["intimacy"], 3),
        "top_sins": state_before["top_sins"],
    }


async def evaluate_and_update(user_id: int, user_msg: str, bot_reply: str,
                              user_display: str = "") -> None:
    if not EMOTION_ENABLED:
        return
    if len(user_msg) < 5 and len(bot_reply) < 20:
        return

    state_before = {
        "mood": mood.get_mood(),
        "trust": image_policy.get_trust(user_id),
        "intimacy": image_policy.get_intimacy(user_id),
        "top_sins": _top_sins(sins.get_state()),
    }

    current_wm = wm_load(user_id)

    name_hint = f" ({user_display})" if user_display else ""
    user_block = (
        f"Пользователь ID {user_id}{name_hint}:\n"
        f"Пользователь: {user_msg[:800]}\n"
        f"Лада: {bot_reply[:800]}\n"
    )

    wm_block = (
        f"\nТЕКУЩАЯ РАБОЧАЯ ПАМЯТЬ ОБ ЭТОМ ЧЕЛОВЕКЕ:\n"
        f"{current_wm or '(пусто)'}\n"
    )

    messages = [
        {"role": "system", "content": COMBINED_PROMPT},
        {"role": "user", "content": (
            f"{user_block}{wm_block}\n"
            f"Сегодня: {datetime.now().strftime('%Y-%m-%d %H:%M')}.\n"
            f"Верни JSON по схеме."
        )},
    ]

    data = await chat_json(messages, "combined")
    if not isinstance(data, dict):
        return

    # ─── EMOTION ───
    emotion = (data.get("emotion") or "").strip()
    if emotion and emotion.upper() != "SKIP" and len(emotion) > 5:
        try:
            em_valence = float(data.get("emotion_valence") or 0.0)
        except (TypeError, ValueError):
            em_valence = 0.0

        meta = _make_meta(user_id, em_valence, state_before)
        faiss_mem.add(emotion, category="emotion",
                      source="emotional_diary", user_id=user_id,
                      meta=meta)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(EMOTIONAL_DIARY, "a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] v={em_valence:+.2f} {emotion}\n")
        print(f"   💭 Эмоция (v={em_valence:+.2f}): {emotion[:80]}")

    # ─── MOOD ───
    m = data.get("mood")
    if m:
        mood.set_mood(m)
        print(f"   🎭 Настроение: {m}")

    # ─── SINS ───
    deltas = data.get("sins_deltas") or {}
    if isinstance(deltas, dict):
        boosted = _apply_asymmetric_multiplier(deltas)
        sins.apply_deltas(boosted)
        changed = {k: v for k, v in boosted.items()
                   if isinstance(v, (int, float)) and abs(v) >= 1}
        if changed:
            parts = [f"{k} {'+' if v > 0 else ''}{v:.1f}"
                     for k, v in changed.items()]
            print(f"   ⚖️ Шкалы: {', '.join(parts)}")

    # ─── TRUST / INTIMACY ───
    try:
        trust_delta = float(data.get("trust_delta") or 0.0)
    except (TypeError, ValueError):
        trust_delta = 0.0
    try:
        intimacy_delta = float(data.get("intimacy_delta") or 0.0)
    except (TypeError, ValueError):
        intimacy_delta = 0.0

    trust_delta = max(-0.10, min(0.10, trust_delta))
    intimacy_delta = max(-0.10, min(0.10, intimacy_delta))

    if trust_delta < 0:
        trust_delta *= MOOD_NEGATIVE_MULTIPLIER
    if intimacy_delta < 0:
        intimacy_delta *= MOOD_NEGATIVE_MULTIPLIER

    if abs(trust_delta) > 0.001 or abs(intimacy_delta) > 0.001:
        current_trust = image_policy.get_trust(user_id)
        current_intimacy = image_policy.get_intimacy(user_id)

        new_trust = max(0.0, min(1.0, current_trust + trust_delta))
        new_intimacy = max(0.0, min(1.0, current_intimacy + intimacy_delta))

        image_policy.set_trust(user_id, trust=new_trust,
                                intimacy=new_intimacy)

        print(f"   🤝 trust: {current_trust:.2f} → {new_trust:.2f} "
              f"({trust_delta:+.3f})")
        print(f"   💗 intimacy: {current_intimacy:.2f} → {new_intimacy:.2f} "
              f"({intimacy_delta:+.3f})")

        relationship.update_status_from_intimacy(user_id, new_intimacy)

    # ─── OBSERVATIONS ───
    observations = data.get("observations") or []
    if isinstance(observations, list):
        for obs in observations:
            if isinstance(obs, str):
                obs_text = obs.strip()
                obs_valence = None
            elif isinstance(obs, dict):
                obs_text = (obs.get("text") or "").strip()
                try:
                    obs_valence = float(obs.get("valence") or 0.0)
                except (TypeError, ValueError):
                    obs_valence = 0.0
            else:
                continue

            valid, norm, why = _is_valid_observation(obs_text)
            if not valid:
                print(f"   👁 ✂️ отклонено ({why}): {obs_text[:80]}")
                continue

            if norm != obs_text:
                print(f"   👁 ✨ нормализовано: "
                      f"{obs_text[:60]!r} → {norm[:60]!r}")

            meta = _make_meta(user_id, obs_valence, state_before)
            ok = faiss_mem.add(norm, category="observation",
                               source="dialog", user_id=user_id,
                               meta=meta)
            v_str = (f" (v={obs_valence:+.2f})"
                     if obs_valence is not None else "")
            print(f"   👁 Наблюдение{v_str}: {norm}"
                  + ("" if ok else "  ⚠️ НЕ СОХРАНЕНО"))

    # ─── WM ───
    wm_update = (data.get("wm_update") or "").strip()
    if not _is_low_quality_wm(wm_update):
        if len(wm_update) > MAX_CHARS:
            wm_update = wm_update[:MAX_CHARS]
        wm_save(user_id, wm_update)
        lines = wm_update.count("\n") + 1
        print(f"   📝 WM обновлена для {user_id} "
              f"({len(wm_update)} симв., {lines} строк)")