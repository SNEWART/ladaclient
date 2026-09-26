# emotions.py — эмоции, настроение, шкалы, trust, intimacy, наблюдения.
#
# Изменения:
#   - LLM возвращает trust_delta и intimacy_delta
#   - Негативные дельты умножаются на MOOD_NEGATIVE_MULTIPLIER
#   - После обновления intimacy повышается статус (lover/husband)

from datetime import datetime
from pathlib import Path

from lada.core.faiss_memory import faiss_mem
from lada.core.llm_json import chat_json
from lada.brain import sins, mood, image_policy, relationship
from config import (
    EMOTION_ENABLED,
    MOOD_NEGATIVE_MULTIPLIER, MOOD_POSITIVE_MULTIPLIER,
)

EMOTIONAL_DIARY = Path("data") / "emotional_diary.log"
EMOTIONAL_DIARY.parent.mkdir(exist_ok=True)


EVAL_PROMPT = (
    "Ты — аналитик диалога Лады с пользователем. Оцени, как этот диалог "
    "повлиял на её состояние.\n\n"
    "Верни СТРОГО JSON без markdown:\n"
    "{\n"
    '  "emotion": "краткая эмоция Лады, 1 предложение, или SKIP",\n'
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
    '  "observations": ["краткий факт о пользователе или пусто"]\n'
    "}\n\n"
    "ПРАВИЛА:\n"
    "- Числа в sins_deltas: -3..3, обычно -1/0/1.\n"
    "- trust_delta: от -0.15 до +0.15. СКОЛЬКО ДОВЕРИЯ изменилось.\n"
    "  · комплимент, поддержка, искренность → +0.01..+0.05\n"
    "  · грубость, унижение, ложь → -0.05..-0.15 (режь сильно)\n"
    "  · нейтральный диалог → 0\n"
    "- intimacy_delta: от -0.10 до +0.10. Насколько БЛИЖЕ стали.\n"
    "  · личные темы, откровенность, флирт → +0.01..+0.05\n"
    "  · грубость, хамство → -0.03..-0.10\n"
    "  · просто болтовня → 0\n"
    "- ВАЖНО: один негативный поступок должен перевешивать десять позитивных.\n"
    "  Если пользователь грубит — trust_delta и intimacy_delta резко минус.\n"
    "- emotion: если диалог живой — опиши что чувствовала. Если технический — SKIP.\n"
    "- observations: 0-3 факта о пользователе (интересы, работа, привычки, обещания).\n"
    "  Формулируй от третьего лица: «Он любит горы», «Работает программистом».\n"
    "  НЕ пиши банальности («он человек», «мы общались»).\n"
    "  Если нового ничего — пустой массив."
)


def _apply_asymmetric_multiplier(deltas: dict) -> dict:
    """Негативные дельты усиливаем, позитивные — как есть."""
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


async def evaluate_and_record(user_msg: str, bot_reply: str, user_id: int | None = None):
    if not EMOTION_ENABLED:
        return
    if len(user_msg) < 5 and len(bot_reply) < 20:
        return

    messages = [
        {"role": "system", "content": EVAL_PROMPT},
        {"role": "user", "content": f"Пользователь: {user_msg}\nЛада: {bot_reply}"},
    ]

    data = await chat_json(messages, "emotions")
    if not isinstance(data, dict):
        return

    # ─── Эмоция ───
    emotion = (data.get("emotion") or "").strip()
    if emotion and emotion.upper() != "SKIP" and len(emotion) > 5:
        faiss_mem.add(emotion, category="emotion", source="emotional_diary", user_id=user_id)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(EMOTIONAL_DIARY, "a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] {emotion}\n")
        print(f"   💭 Эмоция: {emotion[:80]}")

    # ─── Настроение ───
    m = data.get("mood")
    if m:
        mood.set_mood(m)
        print(f"   🎭 Настроение: {m}")

    # ─── Шкалы с асимметричным множителем ───
    deltas = data.get("sins_deltas") or {}
    if isinstance(deltas, dict):
        boosted = _apply_asymmetric_multiplier(deltas)
        sins.apply_deltas(boosted)
        changed = {k: v for k, v in boosted.items()
                   if isinstance(v, (int, float)) and abs(v) >= 1}
        if changed:
            parts = [f"{k} {'+' if v > 0 else ''}{v:.1f}" for k, v in changed.items()]
            print(f"   ⚖️ Шкалы: {', '.join(parts)}")

    # ─── Trust и intimacy ───
    if user_id is not None:
        try:
            trust_delta = float(data.get("trust_delta") or 0.0)
        except (TypeError, ValueError):
            trust_delta = 0.0
        try:
            intimacy_delta = float(data.get("intimacy_delta") or 0.0)
        except (TypeError, ValueError):
            intimacy_delta = 0.0

        # усиливаем негативные дельты
        if trust_delta < 0:
            trust_delta *= MOOD_NEGATIVE_MULTIPLIER
        if intimacy_delta < 0:
            intimacy_delta *= MOOD_NEGATIVE_MULTIPLIER

        if abs(trust_delta) > 0.001 or abs(intimacy_delta) > 0.001:
            current_trust = image_policy.get_trust(user_id)
            current_intimacy = image_policy.get_intimacy(user_id)

            new_trust = max(0.0, min(1.0, current_trust + trust_delta))
            new_intimacy = max(0.0, min(1.0, current_intimacy + intimacy_delta))

            image_policy.set_trust(user_id, trust=new_trust, intimacy=new_intimacy)

            print(f"   🤝 trust: {current_trust:.2f} → {new_trust:.2f} ({trust_delta:+.3f})")
            print(f"   💗 intimacy: {current_intimacy:.2f} → {new_intimacy:.2f} ({intimacy_delta:+.3f})")

            # Обновляем статус по новой intimacy
            relationship.update_status_from_intimacy(user_id, new_intimacy)
            new_status = relationship.get_status(user_id)
            if new_status in relationship.SPECIAL_STATUSES:
                print(f"   💍 Статус: {relationship.get_status_label(user_id)}")

    # ─── Наблюдения ───
    observations = data.get("observations") or []
    if isinstance(observations, list) and user_id is not None:
        for obs in observations:
            if not isinstance(obs, str):
                continue
            obs = obs.strip()
            if len(obs) < 10:
                continue
            faiss_mem.add(obs, category="observation", source="dialog", user_id=user_id)
            print(f"   👁 Наблюдение: {obs}")