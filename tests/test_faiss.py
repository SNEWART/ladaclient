# tests/test_faiss.py

from lada.core.faiss_memory import faiss_mem


def test_faiss_loaded():
    assert faiss_mem is not None


def test_search_returns_list():
    results = faiss_mem.search("тест", k=3, user_id=None, min_score=0.0)
    assert isinstance(results, list)


def test_count_matches_metadata():
    if faiss_mem.index is not None:
        assert faiss_mem.count() == len(faiss_mem.metadata)