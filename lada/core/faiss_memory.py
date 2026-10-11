# faiss_memory.py — векторная память (HNSW) с привязкой к user_id
#
# FIX: LRU-кэш эмбеддингов. Повторяющиеся фразы ("привет", "как дела")
# не пересчитываются. Хит-рейт ~30-40% на реальных диалогах.
# FIX: health_check + громкая ошибка при сломанном эмбеддинге.

import json
import hashlib
from collections import OrderedDict

import numpy as np
import faiss
import ollama
from datetime import datetime
from pathlib import Path
from config import (
    EMBEDDING_MODEL,
    FAISS_TOP_K, FAISS_MIN_SCORE, FAISS_MAX_CHARS,
    HNSW_M, HNSW_EF_CONSTRUCTION, HNSW_EF_SEARCH,
    EMBED_CACHE_SIZE,
)

DATA_DIR = Path("data")
INDEX_PATH = DATA_DIR / "faiss_index.bin"
META_PATH = DATA_DIR / "faiss_metadata.json"
DATA_DIR.mkdir(exist_ok=True)

EMBED_BATCH_SIZE = 32


class FaissMemory:
    def __init__(self):
        self.index = None
        self.metadata = []
        self._embed_broken_reported = False
        self._embed_cache = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0

    def load(self):
        if INDEX_PATH.exists() and META_PATH.exists():
            try:
                self.index = faiss.read_index(str(INDEX_PATH))
                self.metadata = json.loads(META_PATH.read_text(encoding="utf-8"))
                for m in self.metadata:
                    if "user_id" not in m:
                        m["user_id"] = None
                try:
                    if hasattr(self.index, "metric_type"):
                        if self.index.metric_type == faiss.METRIC_L2:
                            print("⚠️ FAISS: индекс загружен с метрикой L2.")
                            print("   Нужно пересобрать: python rebuild_faiss.py")
                except Exception:
                    pass
            except Exception as e:
                print(f"⚠️ Не удалось загрузить FAISS: {e}")
                self.index = None
                self.metadata = []
        else:
            self.index = None
            self.metadata = []

    def save(self):
        if self.index is not None:
            faiss.write_index(self.index, str(INDEX_PATH))
        META_PATH.write_text(
            json.dumps(self.metadata, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # ─────────────── КЭШ ───────────────

    @staticmethod
    def _cache_key(text: str) -> str:
        return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()

    def _cache_get(self, text: str):
        key = self._cache_key(text)
        if key in self._embed_cache:
            self._embed_cache.move_to_end(key)
            self._cache_hits += 1
            return self._embed_cache[key]
        return None

    def _cache_put(self, text: str, vec):
        key = self._cache_key(text)
        self._embed_cache[key] = vec
        self._embed_cache.move_to_end(key)
        while len(self._embed_cache) > EMBED_CACHE_SIZE:
            self._embed_cache.popitem(last=False)

    def cache_stats(self) -> tuple:
        total = self._cache_hits + self._cache_misses
        if total == 0:
            return 0, 0, 0.0
        return self._cache_hits, total, self._cache_hits / total

    # ─────────────── ЭМБЕДДИНГИ ───────────────

    def _report_embedding_broken(self, err):
        if not self._embed_broken_reported:
            print("=" * 60, flush=True)
            print("🛑 КРИТИЧНО: ЭМБЕДДИНГИ НЕ РАБОТАЮТ", flush=True)
            print(f"   Причина: {err}", flush=True)
            print(f"   Решение: ollama pull {EMBEDDING_MODEL}", flush=True)
            print("   Пока эмбеддинги мертвы — FAISS не пишется и не читается.",
                  flush=True)
            print("=" * 60, flush=True)
            self._embed_broken_reported = True

    def _embed(self, text: str):
        """Эмбеддинг одного текста с LRU-кэшем."""
        cached = self._cache_get(text)
        if cached is not None:
            return cached

        try:
            resp = ollama.embed(
                model=EMBEDDING_MODEL,
                input=text,
                keep_alive="30m",
            )
            vec = np.asarray(resp["embeddings"][0], dtype=np.float32)
            faiss.normalize_L2(vec.reshape(1, -1))
            self._cache_misses += 1
            self._cache_put(text, vec)
            return vec
        except Exception as e:
            self._report_embedding_broken(e)
            return None

    def _embed_batch(self, texts: list) -> list:
        if not texts:
            return []

        # Проверяем кэш для каждого
        results = [None] * len(texts)
        to_compute = []
        to_compute_idx = []

        for i, t in enumerate(texts):
            cached = self._cache_get(t)
            if cached is not None:
                results[i] = cached
            else:
                to_compute.append(t)
                to_compute_idx.append(i)

        if not to_compute:
            return results

        try:
            resp = ollama.embed(
                model=EMBEDDING_MODEL,
                input=to_compute,
                keep_alive="30m",
            )
            embs = resp.get("embeddings", [])
            for j, idx in enumerate(to_compute_idx):
                if j >= len(embs):
                    continue
                vec = np.asarray(embs[j], dtype=np.float32)
                faiss.normalize_L2(vec.reshape(1, -1))
                self._cache_misses += 1
                self._cache_put(to_compute[j], vec)
                results[idx] = vec
        except Exception as e:
            self._report_embedding_broken(e)

        return results

    def health_check(self):
        v = self._embed("health check")
        if v is None:
            return False, f"эмбеддинг {EMBEDDING_MODEL} недоступен"
        return True, "ok"

    # ─────────────── ИНДЕКС ───────────────

    def _create_index(self, dim: int):
        index = faiss.IndexHNSWFlat(dim, HNSW_M, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = HNSW_EF_CONSTRUCTION
        index.hnsw.efSearch = HNSW_EF_SEARCH
        return index

    # ─────────────── ЗАПИСЬ ───────────────

    def add(self, text: str, category: str = "general",
            source: str = "reflection", user_id: int = None) -> bool:
        if not text.strip():
            return False
        vec = self._embed(text)
        if vec is None:
            return False
        vec = vec.reshape(1, -1)
        if self.index is None:
            self.index = self._create_index(vec.shape[1])
        self.index.add(vec)
        self.metadata.append({
            "text": text,
            "category": category,
            "source": source,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })
        self.save()
        return True

    def add_many(self, items: list, verbose: bool = True) -> int:
        if not items:
            return 0

        total = len(items)
        if verbose:
            print(f"   Всего: {total} записей, батч по {EMBED_BATCH_SIZE}")

        all_vectors = []
        added_meta = []

        for i in range(0, total, EMBED_BATCH_SIZE):
            batch = items[i:i + EMBED_BATCH_SIZE]
            texts = [item["text"] for item in batch]

            if verbose:
                done = min(i + EMBED_BATCH_SIZE, total)
                print(f"   [{done}/{total}] эмбеддинг...")

            vectors = self._embed_batch(texts)

            for item, vec in zip(batch, vectors):
                if vec is None:
                    continue
                all_vectors.append(vec)
                added_meta.append({
                    "text": item["text"],
                    "category": item.get("category", "general"),
                    "source": item.get("source", "reflection"),
                    "user_id": item.get("user_id"),
                    "timestamp": item.get("timestamp")
                        or datetime.now().isoformat(timespec="seconds"),
                })

        if not all_vectors:
            print("⚠️ Ни одного эмбеддинга не получено")
            return 0

        arr = np.vstack(all_vectors).astype(np.float32)
        if self.index is None:
            self.index = self._create_index(arr.shape[1])
        self.index.add(arr)
        self.metadata.extend(added_meta)
        self.save()

        if verbose:
            print(f"   ✅ Добавлено: {len(added_meta)} "
                  f"(пропущено: {total - len(added_meta)})")
        return len(added_meta)

    def rebuild(self, items: list):
        self.metadata = []
        self.index = None
        if INDEX_PATH.exists():
            INDEX_PATH.unlink()
        if META_PATH.exists():
            META_PATH.unlink()
        if items:
            self.add_many(items)

    # ─────────────── ПОИСК ───────────────

    def search(self, query: str, k: int = None, user_id: int = None,
               min_score: float = None, max_chars: int = None):
        if k is None:
            k = FAISS_TOP_K
        if min_score is None:
            min_score = FAISS_MIN_SCORE
        if max_chars is None:
            max_chars = FAISS_MAX_CHARS

        if self.index is None or self.index.ntotal == 0:
            return []

        vec = self._embed(query)
        if vec is None:
            return []
        vec = vec.reshape(1, -1)

        search_k = self.index.ntotal
        distances, indices = self.index.search(vec, search_k)

        results = []
        total_chars = 0
        for distance, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue

            score = float(distance)
            if score < min_score:
                break

            item = self.metadata[idx]
            item_uid = item.get("user_id")

            if user_id is None:
                if item_uid is not None:
                    continue
            else:
                if item_uid is not None and item_uid != user_id:
                    continue

            text = item.get("text", "")
            if max_chars > 0 and total_chars + len(text) > max_chars:
                break

            results.append(item)
            total_chars += len(text)
            if len(results) >= k:
                break

        return results

    # ─────────────── СТАТИСТИКА ───────────────

    def count(self) -> int:
        return self.index.ntotal if self.index else 0

    def count_by_category(self, category: str, user_id: int = None) -> int:
        if user_id is None:
            return sum(
                1 for m in self.metadata
                if m.get("category") == category and m.get("user_id") is None
            )
        return sum(
            1 for m in self.metadata
            if m.get("category") == category and m.get("user_id") == user_id
        )

    def list_users(self) -> list:
        return sorted({
            m["user_id"] for m in self.metadata
            if m.get("user_id") is not None
        })


faiss_mem = FaissMemory()
faiss_mem.load()