# check_faiss.py — диагностика FAISS

import sys
import traceback
from collections import Counter


def section(title: str):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main():
    try:
        from lada.core.faiss_memory import faiss_mem
    except Exception as e:
        print(f"❌ Не удалось импортировать faiss_mem: {e}")
        traceback.print_exc()
        return 1

    try:
        import faiss
    except ImportError as e:
        print(f"❌ Не установлен faiss: {e}")
        print("   python -m pip install faiss-cpu")
        return 1

    try:
        from config import FAISS_MIN_SCORE, FAISS_TOP_K, FAISS_MAX_CHARS, EMBEDDING_MODEL
    except Exception as e:
        print(f"⚠️  config: {e}")
        FAISS_MIN_SCORE = 0.55
        FAISS_TOP_K = 50
        FAISS_MAX_CHARS = 8000
        EMBEDDING_MODEL = "?"

    section("ОБЩАЯ ИНФОРМАЦИЯ")
    print(f"Модель эмбеддингов: {EMBEDDING_MODEL}")
    print(f"FAISS_MIN_SCORE: {FAISS_MIN_SCORE}")
    print(f"FAISS_TOP_K: {FAISS_TOP_K}")
    print(f"FAISS_MAX_CHARS: {FAISS_MAX_CHARS}")
    print(f"Записей в индексе: {faiss_mem.count()}")
    print(f"Записей в metadata: {len(faiss_mem.metadata)}")

    if faiss_mem.index is not None:
        print(f"Размерность: {faiss_mem.index.d}")
        if hasattr(faiss_mem.index, "metric_type"):
            mt = faiss_mem.index.metric_type
            if mt == faiss.METRIC_INNER_PRODUCT:
                print(f"Метрика: Inner Product ({mt}) ✅")
            elif mt == faiss.METRIC_L2:
                print(f"Метрика: L2 ({mt}) ❌ — запусти rebuild_faiss.py")
            else:
                print(f"Метрика: {mt}")

    section("ПО КАТЕГОРИЯМ")
    cats = Counter(m.get("category", "?") for m in faiss_mem.metadata)
    for cat, n in cats.most_common():
        print(f"  {cat}: {n}")

    section("ПО ПОЛЬЗОВАТЕЛЯМ")
    uids = Counter()
    no_uid = 0
    for m in faiss_mem.metadata:
        uid = m.get("user_id")
        if uid is None:
            no_uid += 1
        else:
            uids[uid] += 1
    print(f"  общие (user_id=None): {no_uid}")
    for uid, n in uids.most_common():
        print(f"  ID {uid}: {n}")

    section("ТЕСТОВЫЕ ЗАПРОСЫ")

    queries = ["привет", "как меня зовут", "работа", "Лада", "кофе"]

    for q in queries:
        print(f"\nЗапрос: {q}")
        try:
            raw_none = faiss_mem.search(q, k=5, user_id=None, min_score=0.0)
            print(f"  user_id=None, min=0.0: {len(raw_none)}")
            for r in raw_none:
                print(f"    · {r.get('category', '?')} | {r['text'][:70]!r}")

            raw_user = faiss_mem.search(q, k=5, user_id=1903274905, min_score=0.0)
            print(f"  user_id=1903274905, min=0.0: {len(raw_user)}")
            for r in raw_user:
                print(f"    · {r.get('category', '?')} | {r['text'][:70]!r}")

            flt = faiss_mem.search(q, k=5, user_id=1903274905, min_score=FAISS_MIN_SCORE)
            print(f"  user_id=1903274905, min={FAISS_MIN_SCORE}: {len(flt)}")
        except Exception as e:
            print(f"  ❌ {e}")
            traceback.print_exc()

    section("ГОТОВО")
    return 0


if __name__ == "__main__":
    sys.exit(main())