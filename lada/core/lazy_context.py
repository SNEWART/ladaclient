# lada/core/lazy_context.py — Ленивая загрузка контекста с кэшированием

import time
import hashlib
import json
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CachedModule:
    """Кэшированный результат модуля."""
    name: str
    content: str
    cost_tokens: int
    timestamp: float = field(default_factory=time.time)
    deps_hash: str = ""

    def is_expired(self, ttl: float) -> bool:
        return (time.time() - self.timestamp) > ttl

    def deps_changed(self, new_hash: str) -> bool:
        return self.deps_hash != new_hash


class LazyContextManager:
    def __init__(self, max_tokens: int = 8192, reserve: int = 1500):
        self.max_tokens = max_tokens
        self.reserve = reserve
        self.available = max_tokens - reserve
        self._cache: Dict[str, CachedModule] = {}
        self.stats = {
            "cache_hits": 0,
            "cache_misses": 0,
            "tokens_saved": 0,
        }

    def _compute_deps_hash(self, deps: Dict[str, Any]) -> str:
        try:
            data = json.dumps(deps, sort_keys=True, default=str)
            return hashlib.md5(data.encode()).hexdigest()[:8]
        except Exception:
            return str(time.time())

    def get_or_compute(
        self,
        module_name: str,
        compute_fn: Callable[[], str],
        deps: Optional[Dict[str, Any]] = None,
        ttl: float = 300.0,
        cost_tokens: int = 100,
    ) -> Optional[str]:
        deps = deps or {}
        deps_hash = self._compute_deps_hash(deps)

        cached = self._cache.get(module_name)
        if cached:
            if not cached.is_expired(ttl) and not cached.deps_changed(deps_hash):
                self.stats["cache_hits"] += 1
                self.stats["tokens_saved"] += cached.cost_tokens
                return cached.content

        self.stats["cache_misses"] += 1

        try:
            content = compute_fn()
            if not content:
                return None

            self._cache[module_name] = CachedModule(
                name=module_name,
                content=content,
                cost_tokens=cost_tokens,
                deps_hash=deps_hash,
            )
            return content

        except Exception as e:
            print(f"   ⚠️ lazy_context: {module_name} failed: {e}")
            return None

    def invalidate(self, module_name: str):
        if module_name in self._cache:
            del self._cache[module_name]

    def invalidate_all(self):
        self._cache.clear()

    def get_stats(self) -> Dict:
        total = self.stats["cache_hits"] + self.stats["cache_misses"]
        hit_rate = (self.stats["cache_hits"] / total * 100) if total > 0 else 0
        return {
            "cache_hits": self.stats["cache_hits"],
            "cache_misses": self.stats["cache_misses"],
            "hit_rate": f"{hit_rate:.1f}%",
            "tokens_saved": self.stats["tokens_saved"],
            "cached_modules": len(self._cache),
        }


MODULE_TTL = {
    # Быстро меняющиеся
    "topic_tracker": 60.0,
    "situational_mood": 120.0,
    "recent_topics": 180.0,

    # Средне меняющиеся
    "promise_tracker": 300.0,
    "companion_model": 300.0,
    "state_vector": 300.0,
    "working_memory": 600.0,
    "silence_reaction": 300.0,   # FIX: добавлено явно

    # Медленно меняющиеся
    "emotional_inertia": 900.0,
    "contacts": 1800.0,
    "relations": 1800.0,
    "weather": 3600.0,

    # Очень редко меняющиеся
    "self_consistency": 7200.0,
    "companion_style": 3600.0,
    "life_log": 1800.0,
    "emotional_peaks": 3600.0,
}


def get_module_deps(module_name: str, user_id: int, **kwargs) -> Dict:
    """
    FIX: раньше здесь были несуществующие таблицы chat_history и
    lada_promises. В db.py таблицы называются messages и promises.
    """
    from lada.core.db import get_conn

    conn = get_conn()

    base_deps = {"user_id": user_id}

    if module_name == "topic_tracker":
        # FIX: COUNT не нуждается в ORDER BY / LIMIT
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM messages WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        base_deps["msg_count"] = row["cnt"] if row else 0

    elif module_name == "promise_tracker":
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM promises "
            "WHERE user_id = ? AND fulfilled = 0",
            (user_id,)
        ).fetchone()
        base_deps["active_promises"] = row["cnt"] if row else 0

    elif module_name == "companion_model":
        from lada.brain import image_policy
        base_deps["trust"] = round(image_policy.get_trust(user_id), 2)
        base_deps["intimacy"] = round(image_policy.get_intimacy(user_id), 2)

    elif module_name == "relations":
        from lada.brain import image_policy, relationship
        base_deps["trust"] = round(image_policy.get_trust(user_id), 2)
        base_deps["intimacy"] = round(image_policy.get_intimacy(user_id), 2)
        base_deps["status"] = relationship.get_status(user_id)

    elif module_name == "weather":
        from lada.core import user_city
        base_deps["city"] = user_city.get_city(user_id) or "unknown"
        base_deps["hour"] = datetime.now().hour

    elif module_name == "mood_state":
        from lada.brain import mood
        base_deps["mood"] = mood.get_mood()
        row = conn.execute(
            "SELECT value FROM lada_state WHERE key = 'mood_valence'"
        ).fetchone()
        if row and row["value"]:
            try:
                data = json.loads(row["value"])
                base_deps["valence"] = round(float(data.get("val", 0.0)), 2)
            except Exception:
                pass

    return base_deps


_global_context_manager = None


def get_lazy_context_manager() -> LazyContextManager:
    global _global_context_manager
    if _global_context_manager is None:
        from config import LLM_NUM_CTX
        _global_context_manager = LazyContextManager(max_tokens=LLM_NUM_CTX)
    return _global_context_manager