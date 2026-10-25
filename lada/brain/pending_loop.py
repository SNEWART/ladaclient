# lada/brain/pending_loop.py — фоновый цикл отправки отложенных инициатив
# и ревизии их актуальности.
#
# Раз в 5 минут:
#   1. При старте (первый запуск) — удаляет все просроченные pending
#      (4.3: обрыв/рестарт — всегда удаляем).
#   2. Проверяет каждый pending:
#      — если scheduled_at <= now и он не писал последние 30 мин → отправить
#      — если он писал за последние 30 мин → отменить (4.7)
#   3. Раз в 2.5 часа ревизия: если mood_valence сильно изменился
#      (разница > 0.5 и знак противоположный) — перегенерировать
#      (макс 2 раза), иначе удалить.

import asyncio
from datetime import datetime, timedelta

from config import (
    INITIATIVE_PENDING_REGEN_MAX,
    INITIATIVE_ENABLED,
    MODEL,
    MAX_HISTORY,
)
from lada.core.db import get_conn
from lada.core.memory import get_history, add_message
from lada.brain import pending as pending_mod
from lada.brain import mood
from lada.brain import initiative as _initiative_mod


PENDING_CHECK_INTERVAL_SEC = 5 * 60
REVIEW_INTERVAL_SEC = 150 * 60

_last_review_at: float = 0.0


def _hours_since_last_user_msg(user_id: int) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT ts FROM messages WHERE user_id = ? AND role = 'user' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row or not row["ts"]:
        return 999.0
    try:
        dt = datetime.fromisoformat(row["ts"])
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return 999.0


def _get_mood_valence() -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM lada_state WHERE key = 'mood_valence'"
    ).fetchone()
    if not row or not row["value"]:
        return 0.0
    try:
        import json
        data = json.loads(row["value"])
        return float(data.get("val", 0.0))
    except Exception:
        return 0.0


# ─── Отправка наступивших pending ───

async def _try_send(client, pending: dict) -> bool:
    user_id = pending["user_id"]

    # 4.7: он писал за последние 30 минут — не отправляем, отменяем
    hours = _hours_since_last_user_msg(user_id)
    if hours * 60 < 30:
        print(f"   ⏭ pending отменён: {user_id} писал {int(hours*60)} мин назад")
        pending_mod.delete_pending(user_id)
        return False

    # Отправляем
    message = pending["message"]
    try:
        await client.send_message(user_id, message)
    except Exception as e:
        print(f"   ⚠️ pending send {user_id}: {e}")
        return False

    try:
        add_message(user_id, "assistant", message, max_history=MAX_HISTORY)
    except Exception:
        pass

    # Счётчик attempt
    att = _initiative_mod._get_attempts(user_id)
    _initiative_mod._save_attempts(
        user_id, att["attempts"] + 1,
        last_attempt=datetime.now().isoformat(timespec="seconds"),
    )

    pending_mod.delete_pending(user_id)
    print(f"   📬 pending отправлен → {user_id}: {message[:80]}")
    return True


# ─── Ревизия: проверка актуальности по настроению ───

async def _review_one(client, pending: dict) -> bool:
    user_id = pending["user_id"]
    old_v = pending.get("mood_valence_at_creation")
    if old_v is None:
        return False
    try:
        old_v = float(old_v)
    except (TypeError, ValueError):
        return False

    new_v = _get_mood_valence()
    delta = new_v - old_v

    # Условие ревизии: разница > 0.5 и знаки противоположные
    if abs(delta) <= 0.5:
        return False
    if (new_v >= 0) == (old_v >= 0):
        return False

    regen_count = int(pending.get("regen_count", 0))

    if regen_count >= INITIATIVE_PENDING_REGEN_MAX:
        print(f"   ⏭ pending {user_id}: 2 регенерации, "
              f"настроение всё ещё не то — удаляю")
        pending_mod.delete_pending(user_id)
        return True

    print(f"   🔄 pending {user_id}: настроение сменилось "
          f"({old_v:+.2f} → {new_v:+.2f}), перегенерирую "
          f"({regen_count + 1}/{INITIATIVE_PENDING_REGEN_MAX})")

    # Генерируем новое с учётом старого настроения
    data = await _initiative_mod._generate_message(
        user_id, old_mood_valence=old_v
    )
    if not data or not data.get("act"):
        pending_mod.delete_pending(user_id)
        return True

    message = data["message"]
    if _initiative_mod._is_template(message):
        pending_mod.delete_pending(user_id)
        return True

    # Обновляем pending
    sched = pending_mod.schedule_for_morning()
    pending_mod.save_pending(
        user_id=user_id,
        message=message,
        reason=data.get("reason", ""),
        scheduled_at=sched,
        mood_valence_at_creation=new_v,
        regen_count=regen_count + 1,
    )
    return True


# ─── Основной цикл ───

async def pending_loop(client):
    if not INITIATIVE_ENABLED:
        print("   ⏸ pending_loop выключен")
        return

    # 4.3: при старте — удаляем все просроченные pending
    try:
        n = pending_mod.clear_all_expired()
        if n:
            print(f"   🧹 pending: удалено {n} просроченных при старте")
    except Exception as e:
        print(f"   ⚠️ pending clear: {e}")

    await asyncio.sleep(60)  # небольшая задержка после старта

    global _last_review_at
    _last_review_at = datetime.now().timestamp()

    while True:
        try:
            # 1. Отправка наступивших
            for p in pending_mod.get_all_pending():
                if pending_mod.is_due(p):
                    await _try_send(client, p)

            # 2. Ревизия раз в 2.5 часа
            now_ts = datetime.now().timestamp()
            if now_ts - _last_review_at >= REVIEW_INTERVAL_SEC:
                _last_review_at = now_ts
                for p in pending_mod.get_all_pending():
                    try:
                        await _review_one(client, p)
                    except Exception as e:
                        print(f"   ⚠️ pending review {p['user_id']}: {e}")

        except Exception as e:
            print(f"   ⚠️ pending_loop: {e}")

        await asyncio.sleep(PENDING_CHECK_INTERVAL_SEC)