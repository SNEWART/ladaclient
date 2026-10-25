# lada/brain/unified_analysis.py — один вызов LLM → все аналитические данные.
#
# Возвращает сразу: эмоцию + valence, mood + mood_valence, sins_deltas,
# trust/intimacy, observations, wm, topic, tone, vibe, companion,
# state_vector. Экономит 5-7 секунд на каждом сообщении.

import asyncio
import json
import re
from datetime import datetime

from lada.core.lancedb_memory import lance_mem as faiss_mem
from lada.core.db import get_conn, ensure_user, Transaction
from lada.core.llm_json import chat_json
from lada.core.working_memory import load as wm_load, save as wm_save, MAX_CHARS
from lada.brain import sins, mood, image_policy, relationship
from config import (
    EMOTION_ENABLED,
    MOOD_NEGATIVE_MULTIPLIER, MOOD_POSITIVE_MULTIPLIER,
)

_UNIFIED_PROMPT = (
    "Ты — аналитик диалога Лады с пользователем. По одному диалогу "
    "верни СТРОГО один JSON без markdown:\n"
    "{\n"
    '  "emotion": "краткая эмоция Лады или SKIP",\n'
    '  "emotion_valence": 0.0,\n'
    '  "mood": "спокойная | весёлая | задумчивая | раздражённая | уставшая | игривая | холодная | тёплая | расстроенная | вдохновлённая | нейтральная",\n'
    '  "mood_valence": 0.0,\n'
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
    '  "observations": [{"text": "факт", "valence": 0.0}],\n'
    '  "wm_update": "полный новый текст рабочей памяти (markdown)",\n'
    '  "topic": "2-6 слов: о чём сейчас",\n'
    '  "tone": "лёгкий | серьёзный | флирт | спор | поддержка | грусть | интимный | нейтральный",\n'
    '  "vibe": "игривое | серьёзное | тёплое | флирт | холодное | поддержка | спор | задумчивое | нейтральное",\n'
    '  "companion_state": "устал | весел | флиртует | грустит | задумчив | зол | занят | спокоен | нейтрален",\n'
    '  "companion_style": "шутить | серьёзно | тепло | держать дистанцию | флиртовать | поддерживать",\n'
    '  "companion_note": "1 короткая фраза (5-12 слов) что с ним",\n'
    '  "state_vector": {"energy": 0.5, "openness": 0.5, "warmth": 0.5, "playfulness": 0.5}\n'
    "}\n\n"
    "ПРАВИЛА:\n"
    "- sins_deltas: -3..3, обычно -1/0/1.\n"
    "\n"
    "🔴 TRUST_DELTA И INTIMACY_DELTA — МЕДЛЕННЫЕ ШКАЛЫ:\n"
    "Текущие значения trust/intimacy — читай ниже, перед диалогом.\n"
    "Ориентиры: обычное сообщение → 0.00; тёплое → +0.01; "
    "искреннее/личное → +0.02; очень сильное → +0.03; "
    "грубость → -0.02..-0.05; предательство → -0.05..-0.10.\n"
    "Если trust > 0.7 или intimacy > 0.7 — дельта НЕ БОЛЬШЕ +0.005.\n"
    "Не бойся 0.00. Большинство сообщений НЕ меняют trust.\n"
    "\n"
    "- emotion_valence: -1.0..+1.0. Окраска эмоции.\n"
    "- mood_valence: -1.0..+1.0. Общая окраска настроения (не то же самое, что emotion_valence).\n"
    "\n"
    "🔴 OBSERVATIONS — ПОСТОЯННЫЕ факты о нём (работа, город, хобби, "
    "характер, питомцы). БЕЗ префикса «пользователь сказал/спросил». "
    "Правильно: «Работает программистом». Неправильно: «Пользователь "
    "сказал, что…». НЕ пиши: пересказ реплик, сиюминутные состояния, "
    "всё про Ладу, «у него кошка Муся» (Муся — кошка Лады). Если нового "
    "постоянного факта нет — пустой массив.\n"
    "valence observation: +0.3..+0.8 приятное, -0.3..-0.8 неприятное, "
    "0.0 нейтральное.\n"
    "\n"
    "- wm_update: обнови рабочую память ТОЛЬКО об этом человеке. "
    "Формат: «### ID <user_id> (<имя>)» + до 20 строк «- ».\n"
    "\n"
    "- topic: 2-6 слов, о чём СЕЙЧАС.\n"
    "- tone: одна строка из списка выше.\n"
    "- vibe: как идёт диалог с её стороны.\n"
    "- companion_state: что с ним СЕЙЧАС.\n"
    "- companion_style: как с ним сейчас говорить.\n"
    "- companion_note: короткая заметка.\n"
    "- state_vector: 4 числа 0.0-1.0.\n"
    "\n"
    "🔴 МОЛЧАНИЕ ЛАДЫ:\n"
    "Если вместо ответа «[промолчала]» — она решила не отвечать. "
    "НЕ накручивай негатив. Снижай раздражение (гнев -1, терпение +1). "
    "mood при «[промолчала]» — чаще «спокойная» или «нейтральная».\n"
    "\n"
    "ВСЁ В ОДНОМ JSON. Ничего лишнего."
)


def _apply_asymmetric_multiplier(deltas: dict) -> dict:
    out = {}
    for k, v in deltas.items():
        try:
            dv = float(v)
        except (TypeError, ValueError):
            out[k] = v
            continue
        out[k] = dv * (MOOD_NEGATIVE_MULTIPLIER if dv < 0 else MOOD_POSITIVE_MULTIPLIER)
    return out


def _top_sins(state: dict, n: int = 3) -> list:
    devs = [(k, v - 50.0) for k, v in state.items() if abs(v - 50.0) > 5]
    devs.sort(key=lambda x: abs(x[1]), reverse=True)
    return [{"name": k, "value": round(d + 50.0, 1), "delta": round(d, 1)} for k, d in devs[:n]]


_OBS_MAX_LEN = 160
_STRIP_PREFIXES = (
    r'^(?:пользователь|он|собеседник)\s+(?:сказал|написал|сообщил|упомянул|рассказал|ответил|поделился|добавил|уточнил),?\s+(?:что\s+)?',
    r'^(?:пользователь|он|собеседник)\s+(?:сообщил|упомянул)\s+о\s+',
    r'^у\s+(?:пользователя|него|собеседника)\s+',
)
_HARD_REJECT = (
    r'^(?:пользователь|он|собеседник)\s+(?:спросил|поинтересовался|задал|пожелал|попросил\s+у|выразил|отреагировал|эмоционально)',
    r'^(?:пользователь|он)\s+написал\s+(?:мне|ей|ладе)',
    r'^(?:пользователь|он)\s+проявляет\s+(?:интерес\s+к\s+ладе|заботу\s+о\s+ладе|внимание\s+к\s+ладе)',
    r'^пользователи\s+', r'^в\s+(?:этом\s+)?диалоге',
)
_OBS_BAD_PATTERNS = (
    re.compile(r'\b(?:о|про|к|с)\s+лад[оеыу]\b', re.IGNORECASE),
    re.compile(r'\bладе\b|\bладу\b|\bладой\b', re.IGNORECASE),
    re.compile(r'\bне\s+мешает\s+ли\b', re.IGNORECASE),
    re.compile(r'\bсейчас\b.*\b(?:устал|рад|груст|злит|бесит|споко)', re.IGNORECASE),
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
    raw = (text or "").strip()
    if not raw:
        return False, raw, "пусто"
    low = raw.lower()
    for pat in _HARD_REJECT:
        if re.search(pat, low):
            return False, raw, "мусор"
    norm = _normalize_observation(raw)
    if len(norm) < 10:
        return False, raw, "коротко"
    if len(norm) > _OBS_MAX_LEN:
        return False, raw, "длинно"
    for pat in _OBS_BAD_PATTERNS:
        if pat.search(norm):
            return False, raw, "паттерн"
    if not re.search(r'[а-яёa-z]{4,}', norm.lower()):
        return False, raw, "нет слов"
    return True, norm, ""


def _clamp01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def _save_state_key(user_id: int, key: str, value: dict):
    ts = datetime.now().isoformat(timespec="seconds")
    with Transaction() as conn:
        if user_id is not None:
            ensure_user(user_id)
        conn.execute(
            "INSERT INTO lada_state (key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
            (key, json.dumps(value, ensure_ascii=False), ts),
        )


def _push_recent_topic(user_id: int, new_topic: str, tone: str):
    from lada.brain import recent_topics, topic_tracker
    old = topic_tracker.get(user_id)
    old_topic = (old or {}).get("topic")
    if old_topic and old_topic != new_topic:
        try:
            recent_topics.push_topic(user_id, old_topic, (old or {}).get("tone"))
        except Exception:
            pass


async def analyze_and_apply(user_id: int, user_msg: str, bot_reply: str, user_display: str = "") -> bool:
    if not EMOTION_ENABLED:
        return False
    if len(user_msg) < 5 and len(bot_reply) < 20:
        return False

    state_before = {
        "mood": mood.get_mood(),
        "trust": image_policy.get_trust(user_id),
        "intimacy": image_policy.get_intimacy(user_id),
        "sins": sins.get_state(),
        "top_sins": _top_sins(sins.get_state()),
    }
    current_wm = wm_load(user_id)
    try:
        status_now = relationship.get_status(user_id)
    except Exception:
        status_now = "neutral"

    cur_trust, cur_intimacy = state_before["trust"], state_before["intimacy"]
    trust_note = " (уже высокий, дельта ≤ +0.005)" if cur_trust > 0.7 else ""
    int_note = " (уже высокий, дельта ≤ +0.005)" if cur_intimacy > 0.7 else ""
    name_hint = f" ({user_display})" if user_display else ""

    user_block = (
        f"Пользователь ID {user_id}{name_hint}:\n\n"
        f"ТЕКУЩЕЕ СОСТОЯНИЕ:\n"
        f"  trust    = {cur_trust:.2f}{trust_note}\n"
        f"  intimacy = {cur_intimacy:.2f}{int_note}\n"
        f"  статус   = {status_now}\n\n"
        f"Диалог:\nПользователь: {user_msg[:800]}\nЛада: {bot_reply[:800]}\n\n"
        f"ТЕКУЩАЯ РАБОЧАЯ ПАМЯТЬ:\n{current_wm or '(пусто)'}\n\n"
        f"Сегодня: {datetime.now().strftime('%Y-%m-%d %H:%M')}.\n"
        f"Верни JSON по схеме."
    )

    messages = [
        {"role": "system", "content": _UNIFIED_PROMPT},
        {"role": "user", "content": user_block}
    ]
    data = await chat_json(messages, "unified")
    if not isinstance(data, dict):
        return False

    # 1. EMOTION
    emotion = (data.get("emotion") or "").strip()
    if emotion and emotion.upper() != "SKIP" and len(emotion) > 5:
        try:
            em_val = float(data.get("emotion_valence") or 0.0)
        except (TypeError, ValueError):
            em_val = 0.0
        meta = {
            "valence": max(-1.0, min(1.0, em_val)),
            "mood_at_time": state_before["mood"],
            "trust_at_time": round(cur_trust, 3),
            "intimacy_at_time": round(cur_intimacy, 3),
            "top_sins": state_before["top_sins"],
        }
        faiss_mem.add(emotion, category="emotion", source="emotional_diary", user_id=user_id, meta=meta)
        print(f"   💭 Эмоция (v={em_val:+.2f}): {emotion[:80]}")

    # 2. MOOD (сохраняем valence в lada_state)
    m = data.get("mood")
    if m:
        try:
            mv = float(data.get("mood_valence") or 0.0)
        except (TypeError, ValueError):
            mv = 0.0
        try:
            from lada.brain import emotional_inertia
            applied = emotional_inertia.check_and_apply(m, "unified")
            if applied:
                print(f"   🎭 Настроение: {m}")
        except Exception:
            mood.set_mood(m)
            print(f"   🎭 Настроение: {m}")
        
        _save_state_key(None, "mood_valence", {"val": mv})

    # 3. SINS
    deltas = data.get("sins_deltas") or {}
    if isinstance(deltas, dict):
        boosted = _apply_asymmetric_multiplier(deltas)
        sins.apply_deltas(boosted)
        changed = {k: v for k, v in boosted.items() if isinstance(v, (int, float)) and abs(v) >= 1}
        if changed:
            parts = [f"{k} {'+' if v > 0 else ''}{v:.1f}" for k, v in changed.items()]
            print(f"   ⚖️ Шкалы: {', '.join(parts)}")

    # 4. TRUST / INTIMACY
    try:
        t_d = float(data.get("trust_delta") or 0.0)
    except (TypeError, ValueError):
        t_d = 0.0
    try:
        i_d = float(data.get("intimacy_delta") or 0.0)
    except (TypeError, ValueError):
        i_d = 0.0

    t_d = max(-0.10, min(0.03, t_d))
    i_d = max(-0.10, min(0.03, i_d))
    if t_d < 0: t_d *= MOOD_NEGATIVE_MULTIPLIER
    if i_d < 0: i_d *= MOOD_NEGATIVE_MULTIPLIER

    if abs(t_d) > 0.0001 or abs(i_d) > 0.0001:
        try:
            from lada.brain import relation_growth
            if abs(t_d) > 0.0001:
                actual, reason = relation_growth.apply_trust(user_id, t_d)
                if abs(actual) >= 0.0001:
                    new = image_policy.get_trust(user_id)
                    print(f"   🤝 trust: {cur_trust:.3f} → {new:.3f} ({actual:+.4f}) [{reason}]")
                else:
                    print(f"   🤝 trust: не растёт [{reason}]")
            if abs(i_d) > 0.0001:
                actual, reason = relation_growth.apply_intimacy(user_id, i_d)
                if abs(actual) >= 0.0001:
                    new = image_policy.get_intimacy(user_id)
                    print(f"   💗 intimacy: {cur_intimacy:.3f} → {new:.3f} ({actual:+.4f}) [{reason}]")
                else:
                    print(f"   💗 intimacy: не растёт [{reason}]")
        except Exception as e:
            print(f"   ⚠️ relation_growth: {e}")

    # 5. OBSERVATIONS
    observations = data.get("observations") or []
    if isinstance(observations, list):
        for obs in observations:
            if isinstance(obs, str):
                obs_text, obs_valence = obs.strip(), None
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
                print(f"   👁 ✂️ ({why}): {obs_text[:80]}")
                continue

            meta = {
                "valence": (None if obs_valence is None else max(-1.0, min(1.0, obs_valence))),
                "mood_at_time": state_before["mood"],
                "trust_at_time": round(cur_trust, 3),
                "intimacy_at_time": round(cur_intimacy, 3),
                "top_sins": state_before["top_sins"],
            }
            ok = faiss_mem.add(norm, category="observation", source="dialog", user_id=user_id, meta=meta)
            v_str = (f" (v={obs_valence:+.2f})" if obs_valence is not None else "")
            print(f"   👁 Наблюдение{v_str}: {norm}" + ("" if ok else "  ⚠️ НЕ СОХРАНЕНО"))

    # 6. WM
    wm_update = (data.get("wm_update") or "").strip()
    if wm_update and len(wm_update) >= 20:
        if wm_update.lower() not in ("без изменений", "unchanged", "(пусто)", "пусто"):
            if len(wm_update) > MAX_CHARS:
                wm_update = wm_update[:MAX_CHARS]
            wm_save(user_id, wm_update)
            print(f"   📝 WM обновлена ({len(wm_update)} симв., {wm_update.count(chr(10)) + 1} строк)")

    # 7. TOPIC + TONE
    topic = (data.get("topic") or "").strip()
    tone = (data.get("tone") or "").strip()
    if topic and 3 <= len(topic) <= 120:
        _push_recent_topic(user_id, topic, tone or "")
        _save_state_key(user_id, f"focus_{user_id}", {
            "topic": topic,
            "tone": tone or "нейтральный",
            "since": datetime.now().isoformat(timespec="seconds"),
        })
        print(f"   🎯 focus: {topic} / {tone or '?'}")

    # 8. VIBE
    vibe = (data.get("vibe") or "").strip().lower()
    if vibe:
        _save_state_key(user_id, f"sit_mood_{user_id}", {
            "vibe": vibe, "why": "", "since": datetime.now().isoformat(timespec="seconds"),
        })

    # 9. COMPANION
    c_state = (data.get("companion_state") or "").strip().lower()
    c_style = (data.get("companion_style") or "").strip().lower()
    c_note = (data.get("companion_note") or "").strip()[:120]
    if c_state or c_style or c_note:
        _save_state_key(user_id, f"companion_{user_id}", {
            "state": c_state or "нейтрален",
            "style": c_style or "тепло",
            "note": c_note,
            "since": datetime.now().isoformat(timespec="seconds"),
        })

    # 10. STATE VECTOR
    vec = data.get("state_vector") or {}
    if isinstance(vec, dict):
        out_vec = {
            "energy": _clamp01(vec.get("energy")),
            "openness": _clamp01(vec.get("openness")),
            "warmth": _clamp01(vec.get("warmth")),
            "playfulness": _clamp01(vec.get("playfulness")),
            "since": datetime.now().isoformat(timespec="seconds"),
        }
        _save_state_key(user_id, f"state_vec_{user_id}", out_vec)

    return True