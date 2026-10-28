# config_optimized.py — ОПТИМИЗИРОВАННЫЕ настройки для 8k контекста
#
# ⚠️ ИНСТРУКЦИЯ:
# 1. Сделай бэкап текущего config.py
# 2. Скопируй эти изменения в свой config.py
# 3. Постепенно возвращай модули, если нужно

# ═══════════════════════════════════════════════════════════════
# ГЛАВНАЯ ПРОБЛЕМА: 12k токенов при лимите 8k
# ═══════════════════════════════════════════════════════════════

# ─────── ИСТОРИЯ И ПАМЯТЬ (было ~6000 токенов) ───────
MAX_HISTORY = 15  # было 30 — урезали вдвое (-3000 токенов)
FAISS_TOP_K = 25  # было 50 — меньше результатов
FAISS_MAX_CHARS = 3000  # было 8000 — КРИТИЧНО! (-1250 токенов)

# ─────── КОНТЕКСТНЫЕ ТРЕКЕРЫ (было ~2000 токенов) ───────
# A1: фокус диалога
TOPIC_TRACKER_ENABLED = True  # важный, оставляем
TOPIC_TRACKER_EVERY_N = 7  # было 5 — реже обновляем

# A2: история тем
RECENT_TOPICS_MAX = 3  # было 5 — меньше тем
RECENT_TOPICS_TTL_MIN = 90  # было 120 — быстрее забываем

# A3: обещания
PROMISE_TRACKER_ENABLED = True  # важный
PROMISE_CHECK_EVERY_N = 3  # было 2 — реже проверяем
PROMISE_STALE_HOURS = 18  # было 24 — быстрее устаревают

# A4: ситуативное настроение
SITUATIONAL_MOOD_ENABLED = True  # оставляем
SITUATIONAL_MOOD_EVERY_N = 5  # было 3 — реже обновляем

# ─────── ТЯЖЁЛЫЕ МОДУЛИ (можно отключить) ───────
# B1: self-consistency (портрет позиций)
SELF_CONSISTENCY_ENABLED = False  # ❌ ОТКЛЮЧИТЬ (~300 токенов)
# Восстанови, если нужна долгосрочная самооценка

# B3+B4: companion model
COMPANION_MODEL_ENABLED = True  # оставляем, важный
COMPANION_MODEL_EVERY_N = 6  # было 4 — реже обновляем

# C2: state vector
STATE_VECTOR_ENABLED = True  # оставляем
STATE_VECTOR_UPDATE_EVERY_N = 5  # было 3 — реже

# C4: emotional inertia
EMOTIONAL_INERTIA_ENABLED = True  # оставляем, легковесный

# ─────── ФОНОВЫЕ СОБЫТИЯ ───────
# Life log (собственная жизнь)
LIFE_LOG_ENABLED = False  # ❌ ОТКЛЮЧИТЬ (~200 токенов)
LIFE_LOG_MAX_ACTIVE = 10  # было 20 — если включишь

# Emotional peaks (пережёвывание)
EMOTIONAL_PEAKS_ENABLED = False  # ❌ ОТКЛЮЧИТЬ (~400 токенов)
# Включи обратно, когда контекст станет больше

# Companion style (стиль собеседника)
COMPANION_STYLE_ENABLED = False  # ❌ ОТКЛЮЧИТЬ (~300 токенов)
# Companion_model уже есть, это доп. анализ

# ─────── МОНОЛОГ ───────
MONOLOGUE_ENABLED = True  # оставляем
MONOLOGUE_INJECT_MAX = 2  # было 3 — меньше вставок

# ─────── MEMORY REACTOR ───────
MEMORY_REACTOR_ENABLED = True
MEMORY_REACTOR_INTERVAL_MINUTES = 60  # было 45 — реже

# ─────── ИНТЕРВАЛЫ ФОНОВЫХ ПРОЦЕССОВ ───────
REFLECTION_INTERVAL = 5400  # 90 мин (было 60)
INITIATIVE_CHECK_INTERVAL_MINUTES = 120  # 2ч (было 90)
SELF_CONSISTENCY_INTERVAL_HOURS = 48  # 2 дня (было 24)

# ═══════════════════════════════════════════════════════════════
# ИТОГО ЭКОНОМИЯ: ~5000-6000 токенов
# ═══════════════════════════════════════════════════════════════
#
# Было:
#   - MAX_HISTORY=30 (~6k токенов)
#   - FAISS_MAX_CHARS=8000 (~2k токенов)
#   - Все модули включены (~3k токенов)
#   - Persona (~1.5k токенов)
#   ИТОГО: ~12.5k токенов
#
# Стало:
#   - MAX_HISTORY=15 (~3k токенов)
#   - FAISS_MAX_CHARS=3000 (~750 токенов)
#   - Критичные модули (~1.5k токенов)
#   - Persona (~1.5k токенов)
#   ИТОГО: ~6.75k токенов ✅
#
# ═══════════════════════════════════════════════════════════════

# ⚠️ ЧТО ПОТЕРЯЛИ:
# - Половину истории диалога (15 вместо 30 сообщений)
# - Большую часть векторной памяти (3k вместо 8k символов)
# - Self-consistency портрет
# - Life log события
# - Emotional peaks
# - Companion style
#
# ✅ ЧТО ОСТАЛОСЬ (самое важное):
# - Текущий фокус диалога (topic_tracker)
# - Обещания (promise_tracker)
# - Модель собеседника (companion_model)
# - Настроение и эмоции (mood, sins)
# - Отношения (trust, intimacy, status)
# - Инициатива и монолог
# - Веб-поиск и генерация изображений
