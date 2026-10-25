# lada/brain/combined_eval.py — обёртка над unified_analysis.
#
# Весь анализ перенесён в lada/brain/unified_analysis.py — один
# вызов Gemma-4 возвращает всё сразу (эмоцию, mood, sins, trust,
# observations, wm, topic, vibe, companion, state_vector).
#
# Этот файл оставлен для совместимости: handlers.py импортирует
# evaluate_and_update отсюда.

from lada.brain.unified_analysis import analyze_and_apply


async def evaluate_and_update(user_id: int, user_msg: str, bot_reply: str,
                              user_display: str = "") -> None:
    try:
        await analyze_and_apply(user_id, user_msg, bot_reply, user_display)
    except Exception as e:
        print(f"   ⚠️ unified analysis: {e}")