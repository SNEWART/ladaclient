# faiss_memory.py — векторная память (HNSW) с привязкой к user_id
#
# Метрика: METRIC_INNER_PRODUCT. Эмбеддинги нормализуются,
# поэтому inner product == косинусная близость (0..1). Больше = лучше.
#
# Эмбеддинги делаются батчами (по 32 текста за запрос) — это критично
# при пересборке: 624 записи обрабатываются за минуту вместо получаса.

import json
import numpy as np
import faiss
import ollama
from datetime import datetime
from pathlib import Path
from config import (
    EMBEDDING_MODEL,
    FAISS_TOP_K, FAISS_MIN_SCORE, FAISS_MAX_CHARS,
    HNSW_M, HNSW_EF_CONSTRUCTION, HNSW_EF_SEARCH,
)

DATA_DIR = Path("data")
INDEX_PATH = DATA_DIR / "faiss_index.bin"
META_PATH = DATA_DIR / "faiss_metadata.json"
DATA_DIR.mkdir(exist_ok=True)

# Сколько текстов за один запрос к Ollama
EMBED_BATCH_SIZE = 32


class FaissMemory:
    def __init__(self):
        self.index = None
        self.metadata = []

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

    def _embed(self, text: str):
        """Одиночный эмбеддинг. Для одного текста."""
        try:
            resp = ollama.embed(
                model=EMBEDDING_MODEL,
                input=text,
                keep_alive="30m",
            )
            vec = np.asarray(resp["embeddings"][0], dtype=np.float32)
            faiss.normalize_L2(vec.reshape(1, -1))
            return vec
        except Exception as e:
            print(f"⚠️ Ошибка эмбеддинга: {e}")
            return None

    def _embed_batch(self, texts: list[str]) -> list:
        """
        Батч эмбеддингов. Один HTTP-запрос — все тексты сразу.
        Возвращает список numpy-векторов в том же порядке,
        или None для тех текстов, где эмбеддинг не удался.
        """
        if not texts:
            return []
        try:
            resp = ollama.embed(
                model=EMBEDDING_MODEL,
                input=texts,
                keep_alive="30m",
            )
            embs = resp.get("embeddings", [])
            if len(embs) != len(texts):
                print(f"⚠️ Ollama вернула {len(embs)} вместо {len(texts)}")
            out = []
            for i in range(len(texts)):
                if i >= len(embs):
                    out.append(None)
                    continue
                vec = np.asarray(embs[i], dtype=np.float32)
                faiss.normalize_L2(vec.reshape(1, -1))
                out.append(vec)
            return out
        except Exception as e:
            print(f"⚠️ Ошибка батч-эмбеддинга: {e}")
            return [None] * len(texts)

    def _create_index(self, dim: int):
        index = faiss.IndexHNSWFlat(dim, HNSW_M, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = HNSW_EF_CONSTRUCTION
        index.hnsw.efSearch = HNSW_EF_SEARCH
        return index

    def add(self, text: str, category: str = "general", source: str = "reflection",
            user_id: int | None = None):
        if not text.strip():
            return
        vec = self._embed(text)
        if vec is None:
            return
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

    def add_many(self, items: list, verbose: bool = True):
        """
        Батчевая вставка. Обрабатывает по EMBED_BATCH_SIZE текстов за раз.
        """
        if not items:
            return

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
                    "timestamp": item.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                })

        if not all_vectors:
            print("⚠️ Ни одного эмбеддинга не получено")
            return

        arr = np.vstack(all_vectors).astype(np.float32)
        if self.index is None:
            self.index = self._create_index(arr.shape[1])
        self.index.add(arr)
        self.metadata.extend(added_meta)
        self.save()

        if verbose:
            print(f"   ✅ Добавлено: {len(added_meta)} (пропущено: {total - len(added_meta)})")

    def rebuild(self, items: list):
        self.metadata = []
        self.index = None
        if INDEX_PATH.exists():
            INDEX_PATH.unlink()
        if META_PATH.exists():
            META_PATH.unlink()
        if items:
            self.add_many(items)

    def search(self, query: str, k: int = None, user_id: int | None = None,
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

        # Ищем по всей базе — фильтр по user_id может отсеять топ-kanдидаты
        search_k = self.index.ntotal
        distances, indices = self.index.search(vec, search_k)

        results = []
        total_chars = 0
        for distance, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue

            # METRIC_INNER_PRODUCT → от большего к меньшему
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

    def count(self) -> int:
        return self.index.ntotal if self.index else 0

    def count_by_category(self, category: str, user_id: int | None = None) -> int:
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