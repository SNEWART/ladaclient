# context_manager.py — Умный менеджер контекста для ladaclient
#
# Решает проблему: 12k токенов при лимите 8k
# Подход: динамически выбирает, какие модули включить в промпт

import tiktoken
from typing import Dict, List, Tuple, Optional

# Примерная оценка токенов (можно настроить точнее)
TOKEN_ESTIMATES = {
    "persona": 1500,
    "header": 200,
    "history_per_msg": 200,  # ~200 токенов на сообщение
    "topic_tracker": 150,
    "recent_topics": 120,
    "situational_mood": 100,
    "promise_tracker": 180,
    "emotional_inertia": 80,
    "state_vector": 150,
    "companion_model": 250,
    "companion_style": 200,
    "contacts": 150,
    "working_memory": 200,
    "self_consistency": 300,
    "life_log": 200,
    "silence_reaction": 100,
    "emotional_peaks": 400,
    "mood_state": 150,
    "relations": 200,
    "weather": 100,
    "style_brief": 500,
    "faiss_per_1k": 250,  # ~250 токенов на 1000 символов FAISS
}


class ContextBudget:
    """Управление бюджетом токенов для промпта."""

    def __init__(self, max_tokens: int = 8192, reserve: int = 1000):
        """
        Args:
            max_tokens: Максимальный лимит контекста модели
            reserve: Резерв для ответа модели
        """
        self.max_tokens = max_tokens
        self.reserve = reserve
        self.available = max_tokens - reserve
        self.used = 0

    def can_afford(self, cost: int) -> bool:
        """Можем ли добавить модуль с такой стоимостью?"""
        return (self.used + cost) <= self.available

    def allocate(self, cost: int) -> bool:
        """Зарезервировать токены. Возвращает True если успешно."""
        if self.can_afford(cost):
            self.used += cost
            return True
        return False

    def remaining(self) -> int:
        """Сколько токенов осталось?"""
        return self.available - self.used


class ModulePriority:
    """Приоритеты модулей для включения в промпт."""

    # Уровни приоритета
    CRITICAL = 1    # Всегда включать (persona, header, текущее сообщение)
    HIGH = 2        # Очень важно (история, отношения, настроение)
    MEDIUM = 3      # Важно (companion_model, promise_tracker)
    LOW = 4         # Желательно (peaks, life_log, style)
    OPTIONAL = 5    # Можно опустить (self_consistency)

    @staticmethod
    def get_priority(module_name: str, context: Dict) -> int:
        """
        Определить приоритет модуля в зависимости от контекста.

        Args:
            module_name: имя модуля
            context: контекст диалога (user_id, intimacy, последние сообщения и т.д.)
        """
        # Критичные модули
        if module_name in ("persona", "header", "current_message", "style_brief"):
            return ModulePriority.CRITICAL

        # Высокий приоритет
        if module_name in ("history", "mood_state", "relations"):
            return ModulePriority.HIGH

        # История диалога
        if module_name == "topic_tracker":
            return ModulePriority.HIGH

        # Обещания — важны, если есть активные
        if module_name == "promise_tracker":
            has_promises = context.get("has_active_promises", False)
            return ModulePriority.HIGH if has_promises else ModulePriority.MEDIUM

        # Companion model — важен для близких отношений
        if module_name == "companion_model":
            intimacy = context.get("intimacy", 0.0)
            return ModulePriority.HIGH if intimacy > 0.5 else ModulePriority.MEDIUM

        # Emotional peaks — важны при сильных эмоциях
        if module_name == "emotional_peaks":
            mood_valence = abs(context.get("mood_valence", 0.0))
            return ModulePriority.MEDIUM if mood_valence > 1.5 else ModulePriority.LOW

        # FAISS память
        if module_name == "faiss_memory":
            # Приоритет зависит от длины текущего сообщения
            msg_len = context.get("message_length", 0)
            if msg_len > 100:  # Длинное сообщение — нужна память
                return ModulePriority.HIGH
            return ModulePriority.MEDIUM

        # Silence reaction — важна, если долго молчал
        if module_name == "silence_reaction":
            hours_silence = context.get("hours_since_last_msg", 0)
            return ModulePriority.HIGH if hours_silence > 6 else ModulePriority.OPTIONAL

        # Working memory
        if module_name == "working_memory":
            has_wm = context.get("has_working_memory", False)
            return ModulePriority.HIGH if has_wm else ModulePriority.OPTIONAL

        # Остальное
        return {
            "recent_topics": ModulePriority.MEDIUM,
            "situational_mood": ModulePriority.MEDIUM,
            "emotional_inertia": ModulePriority.MEDIUM,
            "state_vector": ModulePriority.MEDIUM,
            "companion_style": ModulePriority.LOW,
            "contacts": ModulePriority.LOW,
            "life_log": ModulePriority.LOW,
            "self_consistency": ModulePriority.OPTIONAL,
            "weather": ModulePriority.LOW,
        }.get(module_name, ModulePriority.OPTIONAL)


def calculate_history_budget(budget: ContextBudget, max_history: int = 30) -> int:
    """
    Сколько сообщений истории можем себе позволить?

    Args:
        budget: бюджет токенов
        max_history: максимальное количество сообщений

    Returns:
        Количество сообщений, которое влезет в бюджет
    """
    cost_per_msg = TOKEN_ESTIMATES["history_per_msg"]
    remaining = budget.remaining()

    # Резервируем минимум для других модулей
    MIN_OTHER_MODULES = 2000
    available_for_history = max(0, remaining - MIN_OTHER_MODULES)

    max_affordable = available_for_history // cost_per_msg
    return min(max_affordable, max_history)


def calculate_faiss_budget(budget: ContextBudget, max_chars: int = 8000) -> int:
    """
    Сколько символов FAISS памяти можем себе позволить?

    Args:
        budget: бюджет токенов
        max_chars: максимальное количество символов

    Returns:
        Количество символов FAISS, которое влезет
    """
    remaining = budget.remaining()
    cost_per_1k = TOKEN_ESTIMATES["faiss_per_1k"]

    # Резервируем для других модулей
    MIN_OTHER = 1500
    available = max(0, remaining - MIN_OTHER)

    max_chars_affordable = (available * 1000) // cost_per_1k
    return min(max_chars_affordable, max_chars)


def build_optimized_modules_list(
    context: Dict,
    budget: ContextBudget,
    enabled_modules: Dict[str, bool]
) -> List[str]:
    """
    Построить список модулей для включения в промпт, соблюдая бюджет.

    Args:
        context: контекст диалога
        budget: бюджет токенов
        enabled_modules: какие модули вообще включены в конфиге

    Returns:
        Список имён модулей для включения
    """
    # Собираем все модули с приоритетами
    modules = []
    for module_name in TOKEN_ESTIMATES.keys():
        if module_name in ("history_per_msg", "faiss_per_1k"):
            continue  # Особая обработка

        if module_name in ("persona", "header", "current_message", "style_brief"):
            # Критичные всегда включены
            modules.append((module_name, ModulePriority.CRITICAL, TOKEN_ESTIMATES[module_name]))
        else:
            # Проверяем, включён ли в конфиге
            config_key = f"{module_name}_enabled"
            if enabled_modules.get(config_key, True):
                priority = ModulePriority.get_priority(module_name, context)
                cost = TOKEN_ESTIMATES.get(module_name, 100)
                modules.append((module_name, priority, cost))

    # Сортируем по приоритету
    modules.sort(key=lambda x: (x[1], -x[2]))  # сначала по приоритету, потом по размеру (больше = важнее)

    # Добавляем модули, пока хватает бюджета
    selected = []
    for module_name, priority, cost in modules:
        if budget.allocate(cost):
            selected.append(module_name)
        else:
            # Бюджет кончился
            print(f"   ⚠️ context_manager: пропускаем '{module_name}' (нехватка {cost - budget.remaining()} токенов)")

    return selected


def optimize_prompt_context(
    user_id: int,
    message: str,
    max_tokens: int = 8192,
    config_enabled: Optional[Dict[str, bool]] = None
) -> Dict:
    """
    Главная функция: оптимизировать контекст промпта под бюджет токенов.

    Args:
        user_id: ID пользователя
        message: текущее сообщение
        max_tokens: лимит контекста модели
        config_enabled: словарь включённых модулей из config.py

    Returns:
        Словарь с рекомендациями:
        {
            "max_history": сколько сообщений истории включить,
            "faiss_max_chars": сколько символов FAISS памяти,
            "enabled_modules": список модулей для включения,
            "estimated_tokens": примерная оценка итоговых токенов
        }
    """
    if config_enabled is None:
        config_enabled = {}

    budget = ContextBudget(max_tokens=max_tokens, reserve=1500)

    # Базовые затраты (всегда есть)
    budget.allocate(TOKEN_ESTIMATES["persona"])
    budget.allocate(TOKEN_ESTIMATES["header"])
    budget.allocate(TOKEN_ESTIMATES["style_brief"])

    # Контекст для определения приоритетов
    # (в реальности заполняется из БД)
    context = {
        "user_id": user_id,
        "message_length": len(message),
        "intimacy": 0.5,  # TODO: брать из БД
        "mood_valence": 0.0,  # TODO: брать из БД
        "hours_since_last_msg": 0,  # TODO: считать
        "has_active_promises": False,  # TODO: проверять
        "has_working_memory": False,  # TODO: проверять
    }

    # Выбираем модули
    enabled_modules_list = build_optimized_modules_list(context, budget, config_enabled)

    # Считаем, сколько истории и FAISS влезет
    max_history = calculate_history_budget(budget, max_history=30)
    faiss_max_chars = calculate_faiss_budget(budget, max_chars=8000)

    # Резервируем под историю и FAISS
    budget.allocate(max_history * TOKEN_ESTIMATES["history_per_msg"])
    budget.allocate((faiss_max_chars // 1000) * TOKEN_ESTIMATES["faiss_per_1k"])

    estimated_total = budget.used

    print(f"   📊 context_manager: {estimated_total}/{max_tokens} токенов")
    print(f"      история: {max_history} сообщ, FAISS: {faiss_max_chars} симв")
    print(f"      модулей: {len(enabled_modules_list)}")

    return {
        "max_history": max_history,
        "faiss_max_chars": faiss_max_chars,
        "enabled_modules": enabled_modules_list,
        "estimated_tokens": estimated_total,
        "budget_remaining": budget.remaining(),
    }


# ═══════════════════════════════════════════════════════════════
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Симуляция: короткое сообщение
    result = optimize_prompt_context(
        user_id=123,
        message="Привет, как дела?",
        max_tokens=8192,
    )

    print("\nРезультат оптимизации:")
    print(f"  MAX_HISTORY: {result['max_history']}")
    print(f"  FAISS_MAX_CHARS: {result['faiss_max_chars']}")
    print(f"  Модули: {', '.join(result['enabled_modules'][:10])}...")
    print(f"  Примерно токенов: {result['estimated_tokens']}")
    print(f"  Остаток: {result['budget_remaining']}")
