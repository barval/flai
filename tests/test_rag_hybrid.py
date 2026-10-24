# tests/test_rag_hybrid.py
"""Hybrid retrieval: full-text mirror, lexical search and RRF fusion."""

import pytest

from modules.rag import RagModule, rrf_fuse

pytestmark = pytest.mark.requires_db


def _key(chunk: dict) -> tuple:
    return (str(chunk.get("doc_id")), int(chunk.get("chunk_index") or 0))


def _cleanup(user_id: str) -> None:
    from app.database import get_db

    with get_db() as conn:
        conn.cursor().execute("DELETE FROM rag_chunks WHERE user_id = %s", (user_id,))


@pytest.mark.unit
def test_rrf_prefers_a_chunk_both_retrievers_found():
    dense = [{"doc_id": "d1", "chunk_index": 0}, {"doc_id": "d2", "chunk_index": 0}]
    lexical = [{"doc_id": "d2", "chunk_index": 0}, {"doc_id": "d3", "chunk_index": 0}]

    fused = rrf_fuse([dense, lexical], key=_key, k=60)

    assert [chunk["doc_id"] for chunk, _score in fused] == ["d2", "d1", "d3"]


@pytest.mark.unit
def test_rrf_keeps_every_chunk_once():
    dense = [{"doc_id": "d1", "chunk_index": 0}]
    lexical = [{"doc_id": "d1", "chunk_index": 0}, {"doc_id": "d2", "chunk_index": 0}]

    fused = rrf_fuse([dense, lexical], key=_key, k=60)

    assert len(fused) == 2
    assert fused[0][1] > fused[1][1]


def test_chunks_are_mirrored_and_removed(test_app):
    with test_app.app_context():
        _cleanup("hybriduser")
        module = RagModule()

        module._store_chunks("hybriduser", "doc-1", "cv.pdf", ["Работал в Москве", "Отпуск в Сочи"])
        rows = module._lexical_search("hybriduser", "Москве", 10)
        assert rows and rows[0]["doc_id"] == "doc-1"

        module._delete_chunks("doc-1", "hybriduser")
        assert module._lexical_search("hybriduser", "Москве", 10) == []


def test_lexical_search_respects_the_document_scope(test_app):
    with test_app.app_context():
        _cleanup("hybriduser")
        module = RagModule()
        module._store_chunks("hybriduser", "doc-1", "one.pdf", ["Москва, работа в банке"])
        module._store_chunks("hybriduser", "doc-2", "two.pdf", ["Москва, работа в телекоме"])

        scoped = module._lexical_search("hybriduser", "Москва", 10, ["doc-2"])

        assert [row["doc_id"] for row in scoped] == ["doc-2"]
        _cleanup("hybriduser")


def test_reindexing_replaces_the_mirrored_chunks(test_app):
    with test_app.app_context():
        _cleanup("hybriduser")
        module = RagModule()
        module._store_chunks("hybriduser", "doc-1", "cv.pdf", ["старый текст про Москву"])

        module._store_chunks("hybriduser", "doc-1", "cv.pdf", ["новый текст про Саров"])

        assert module._lexical_search("hybriduser", "Москву", 10) == []
        assert module._lexical_search("hybriduser", "Саров", 10)[0]["doc_id"] == "doc-1"
        _cleanup("hybriduser")


@pytest.mark.unit
def test_lexical_terms_prefer_proper_nouns():
    module = RagModule()

    assert module._lexical_terms("Сколько рабочих мест было у Валерия в Москве?") == ["валерия", "москве"]
    assert module._lexical_terms("Для чего Валерий использовал bash?") == ["валерий", "bash"]


@pytest.mark.unit
def test_lexical_terms_fall_back_to_all_words():
    module = RagModule()

    assert module._lexical_terms("как настроить почтовый сервер") == [
        "как",
        "настроить",
        "почтовый",
        "сервер",
    ]


@pytest.mark.unit
def test_tsquery_terms_cover_both_stems():
    """The stemmer maps "Саров" and "Сарове" to different lexemes; both prefixes stay."""
    assert RagModule._tsquery_terms(["сарове"]) == ["сарове:*", "саров:*"]
    assert RagModule._tsquery_terms(["баш"]) == ["баш:*"]


@pytest.mark.unit
def test_rerank_scores_are_normalised_and_ordered(monkeypatch):
    """Raw logits become comparable scores with the best candidate at 1.0."""
    from app import reranker

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"index": 0, "relevance_score": -4.28},
                    {"index": 1, "relevance_score": -10.99},
                    {"index": 2, "relevance_score": -10.79},
                ]
            }

    monkeypatch.setattr(reranker.requests, "post", lambda *a, **k: _Response())

    scores = reranker.rerank("q", ["a", "b", "c"], "http://llama:8080")

    assert scores is not None
    assert scores[0] == pytest.approx(1.0)
    assert scores[0] > scores[2] > scores[1]


@pytest.mark.unit
def test_rerank_returns_none_when_the_service_is_down(monkeypatch):
    from app import reranker

    def boom(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(reranker.requests, "post", boom)

    assert reranker.rerank("q", ["a"], "http://llama:8080") is None
