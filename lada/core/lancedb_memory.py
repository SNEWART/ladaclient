# lancedb_memory.py — векторная память на LanceDB.
#
# Обогащение: каждая запись может нести meta-словарь (valence, mood_at_time,
# trust_at_time, intimacy_at_time, top_sins). Храним как JSON-строку в поле
# meta_json. Для старых таблиц колонка добавляется автоматически при загрузке.
#
# FIX: для эмбеддингов передаём num_ctx=512 — иначе bge-m3 просит 4096
# и съедает лишние 3 ГБ VRAM впустую.

import hashlib
import json
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import numpy as np
import lancedb
import ollama
from config import (
    EMBEDDING_MODEL,
    FAISS_TOP_K, FAISS_MIN_SCORE, FAISS_MAX_CHARS,
    EMBED_CACHE_SIZE,
)

DATA_DIR = Path("data")
LANCEDB_DIR = DATA_DIR / "lancedb"
TABLE_NAME = "memory"

META_CACHE_TTL = 5.0


class LanceMemory:
    def __init__(self):
        self.db = lancedb.connect(str(LANCEDB_DIR))
        self.table = None
        self._embed_broken_reported = False
        self._embed_cache = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._meta_cache = None
        self._meta_cache_time = 0.0

    # ─────────────── ЗАГРУЗКА ───────────────

    def load(self):
        try:
            self.table = self.db.open_table(TABLE_NAME)
            print(f"   🗄 LanceDB: таблица '{TABLE_NAME}' открыта "
                  f"({self.table.count_rows()} записей)")
            self._ensure_meta_column()
        except Exception:
            print(f"   🗄 LanceDB: таблица '{TABLE_NAME}' не найдена, "
                  f"будет создана при первой записи")
            self.table = None

    def _ensure_meta_column(self):
        """Добавляет meta_json, если её нет в старой таблице."""
        if self.table is None:
            return
        try:
            names = list(self.table.schema.names)
            if "meta_json" in names:
                return
            self.table.add_columns({"meta_json": "cast('' as string)"})
            print("   🗄 LanceDB: добавлено поле meta_json для старых записей")
        except Exception as e:
            print(f"   ⚠️ LanceDB: не удалось добавить meta_json ({e}) — "
                  f"эмоциональные метки сохраняться не будут")

    def _ensure_table(self, sample_vector: list):
        if self.table is not None:
            return
        import pyarrow as pa
        dim = len(sample_vector)
        schema = pa.schema([
            pa.field("vector", pa.list_(pa.float32(), dim)),
            pa.field("text", pa.string()),
            pa.field("category", pa.string()),
            pa.field("source", pa.string()),
            pa.field("user_id", pa.int64()),
            pa.field("timestamp", pa.string()),
            pa.field("meta_json", pa.string()),
        ])
        self.table = self.db.create_table(TABLE_NAME, schema=schema)

    def save(self):
        pass

    # ─────────────── METADATA ───────────────

    @property
    def metadata(self) -> list:
        now = time.time()
        if (self._meta_cache is not None
                and now - self._meta_cache_time < META_CACHE_TTL):
            return self._meta_cache

        if self.table is None:
            self._meta_cache = []
            self._meta_cache_time = now
            return []

        try:
            tbl = self.table.to_arrow()
            cols = {name: tbl.column(name).to_pylist()
                    for name in tbl.column_names}
        except Exception as e:
            print(f"   ⚠️ LanceDB metadata: {e}")
            self._meta_cache = []
            self._meta_cache_time = now
            return []

        n = len(cols.get("text", []))
        has_meta = "meta_json" in cols
        out = []
        for i in range(n):
            uid = cols["user_id"][i] if "user_id" in cols else None
            if uid is not None and int(uid) == -1:
                uid = None

            meta = {}
            if has_meta:
                raw = cols["meta_json"][i]
                if raw:
                    try:
                        meta = json.loads(raw)
                    except Exception:
                        meta = {}

            item = {
                "text": cols["text"][i] or "",
                "category": (cols["category"][i]
                             if "category" in cols else "general"),
                "source": (cols["source"][i]
                           if "source" in cols else "reflection"),
                "user_id": uid,
                "timestamp": (cols["timestamp"][i]
                              if "timestamp" in cols else ""),
            }
            if meta:
                item["_meta"] = meta
                item["valence"] = meta.get("valence")
                item["mood_at_time"] = meta.get("mood_at_time")
                item["trust_at_time"] = meta.get("trust_at_time")
                item["intimacy_at_time"] = meta.get("intimacy_at_time")
                item["top_sins"] = meta.get("top_sins")
            out.append(item)

        self._meta_cache = out
        self._meta_cache_time = now
        return out

    def _invalidate_meta_cache(self):
        self._meta_cache = None
        self._meta_cache_time = 0.0

    # ─────────────── КЭШ ЭМБЕДДИНГОВ ───────────────

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

    def cache_stats(self):
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
            print("=" * 60, flush=True)
            self._embed_broken_reported = True

    def _embed(self, text: str):
        cached = self._cache_get(text)
        if cached is not None:
            return cached
        try:
            resp = ollama.embed(
                model=EMBEDDING_MODEL,
                input=text,
                keep_alive="30m",
                options={"num_ctx": 512},
            )
            vec = np.asarray(resp["embeddings"][0], dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            self._cache_misses += 1
            self._cache_put(text, vec)
            return vec
        except Exception as e:
            self._report_embedding_broken(e)
            return None

    def _embed_batch(self, texts: list) -> list:
        if not texts:
            return []

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
                options={"num_ctx": 512},
            )
            embs = resp.get("embeddings", [])
            for j, idx in enumerate(to_compute_idx):
                if j >= len(embs):
                    continue
                vec = np.asarray(embs[j], dtype=np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
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

    # ─────────────── ЗАПИСЬ ───────────────

    def add(self, text: str, category: str = "general",
            source: str = "reflection", user_id: int = None,
            meta: dict | None = None) -> bool:
        if not text.strip():
            return False
        vec = self._embed(text)
        if vec is None:
            return False
        self._ensure_table(vec.tolist())

        uid = user_id if user_id is not None else -1
        meta_str = ""
        if meta:
            try:
                meta_str = json.dumps(meta, ensure_ascii=False)
            except Exception:
                meta_str = ""

        record = {
            "vector": vec.tolist(),
            "text": text,
            "category": category,
            "source": source,
            "user_id": uid,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "meta_json": meta_str,
        }
        self.table.add([record])
        self._invalidate_meta_cache()
        return True

    def add_many(self, items: list, verbose: bool = True) -> int:
        if not items:
            return 0

        records = []
        for item in items:
            vec = self._embed(item["text"])
            if vec is None:
                continue
            self._ensure_table(vec.tolist())
            uid = item.get("user_id")
            meta = item.get("meta")
            meta_str = ""
            if meta:
                try:
                    meta_str = json.dumps(meta, ensure_ascii=False)
                except Exception:
                    meta_str = ""
            records.append({
                "vector": vec.tolist(),
                "text": item["text"],
                "category": item.get("category", "general"),
                "source": item.get("source", "reflection"),
                "user_id": uid if uid is not None else -1,
                "timestamp": item.get("timestamp")
                    or datetime.now().isoformat(timespec="seconds"),
                "meta_json": meta_str,
            })

        if records:
            self.table.add(records)
            self._invalidate_meta_cache()

        if verbose:
            print(f"   ✅ LanceDB: добавлено {len(records)} "
                  f"(пропущено: {len(items) - len(records)})")
        return len(records)

    def rebuild(self, items: list):
        try:
            self.db.drop_table(TABLE_NAME)
        except Exception:
            pass
        self.table = None
        self._invalidate_meta_cache()
        if items:
            self.add_many(items)

    # ─────────────── ПОИСК ───────────────

    def search(self, query: str, k: int = None, user_id: int = None,
               min_score: float = None, max_chars: int = None):
        if self.table is None:
            return []
        if k is None:
            k = FAISS_TOP_K
        if min_score is None:
            min_score = FAISS_MIN_SCORE
        if max_chars is None:
            max_chars = FAISS_MAX_CHARS

        vec = self._embed(query)
        if vec is None:
            return []

        if user_id is not None:
            where = f"user_id = {user_id} OR user_id = -1"
        else:
            where = "user_id = -1"

        try:
            q = (
                self.table.search(vec.tolist())
                .metric("cosine")
                .where(where, prefilter=True)
                .limit(k)
            )
            results = q.to_list()
        except Exception as e:
            print(f"   ⚠️ LanceDB search error: {e}")
            return []

        out = []
        total_chars = 0
        for r in results:
            score = 1.0 - float(r.get("_distance", 1.0))
            if score < min_score:
                continue
            text = r.get("text", "")
            if max_chars > 0 and total_chars + len(text) > max_chars:
                break

            meta = {}
            raw_meta = r.get("meta_json")
            if raw_meta:
                try:
                    meta = json.loads(raw_meta)
                except Exception:
                    meta = {}

            item = {
                "text": text,
                "category": r.get("category", "general"),
                "source": r.get("source", "reflection"),
                "user_id": (None if r.get("user_id") == -1
                            else r.get("user_id")),
                "timestamp": r.get("timestamp", ""),
                "_score": score,
            }
            if meta:
                item["_meta"] = meta
                item["valence"] = meta.get("valence")
                item["mood_at_time"] = meta.get("mood_at_time")
                item["trust_at_time"] = meta.get("trust_at_time")
                item["intimacy_at_time"] = meta.get("intimacy_at_time")
                item["top_sins"] = meta.get("top_sins")

            out.append(item)
            total_chars += len(text)

        return out

    # ─────────────── СТАТИСТИКА ───────────────

    def count(self) -> int:
        if self.table is None:
            return 0
        return self.table.count_rows()

    def count_by_category(self, category: str, user_id: int = None) -> int:
        if self.table is None:
            return 0
        try:
            if user_id is None:
                filt = f"category = '{category}' AND user_id = -1"
            else:
                filt = f"category = '{category}' AND user_id = {user_id}"
            return self.table.count_rows(filter=filt)
        except Exception:
            return 0

    def list_users(self) -> list:
        if self.table is None:
            return []
        try:
            tbl = self.table.to_arrow()
            col = tbl.column("user_id").to_pylist()
            uids = {int(u) for u in col if u is not None and int(u) >= 0}
            return sorted(uids)
        except Exception:
            return []


lance_mem = LanceMemory()
lance_mem.load()