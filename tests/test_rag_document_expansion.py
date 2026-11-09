# tests/test_rag_document_expansion.py
"""A small document is passed whole when one chunk cannot answer the question.

Measured live on the noise law: the question "can repair works be done now"
retrieved article 4 ("repair works") as its only relevant chunk, while the quiet
hours lived in article 2, thirteen places down, and 96 % of the window stayed
unused. The whole law answered exactly. A large document keeps the chunk-level
result, and the rest of the retrieval still gets the room that is left.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from app.rag_budget import RetrievalBudget
from app.utils import estimate_tokens

THRESHOLD = 0.3


def _module():
    from modules.rag import RagModule

    app = Mock()
    app.config = {
        "QDRANT_URL": "http://test-qdrant:6333",
        "QDRANT_API_KEY": "test-api-key",
        "RAG_CHUNK_SIZE": 500,
        "RAG_CHUNK_OVERLAP": 50,
        "RAG_TOP_K": 15,
        "RAG_RELEVANCE_THRESHOLD_DEFAULT": THRESHOLD,
        "RAG_RELEVANCE_THRESHOLD_REASONING": 0.7,
    }
    app.logger = Mock()
    with patch("modules.rag.QdrantClient") as client:
        client.return_value.get_collections.return_value = MagicMock(collections=[])
        module = RagModule(app)
    module.available = True
    module.hybrid_enabled = False
    module._get_embedding = Mock(return_value=[0.1])
    module.logger = Mock()
    return module


def _hit(doc_id, chunk_index, text, score, user_id="user", filename=None):
    hit = Mock()
    hit.payload = {
        "doc_id": doc_id,
        "user_id": user_id,
        "chunk_index": chunk_index,
        "filename": filename or f"{doc_id}.txt",
        "text": text,
    }
    hit.score = score
    return hit


def _point(doc_id, chunk_index, text, user_id="user"):
    point = Mock()
    point.payload = {
        "doc_id": doc_id,
        "user_id": user_id,
        "chunk_index": chunk_index,
        "filename": f"{doc_id}.txt",
        "text": text,
    }
    return point


def _budget(tokens: int, top_k: int = 1, coverage: int = 3) -> RetrievalBudget:
    return RetrievalBudget(top_k=top_k, coverage_files=coverage, fragments=4, tokens=tokens, tokens_per_fragment=200)


def _search(module, budget, hits, document_points):
    module.qdrant_client = MagicMock()
    module.qdrant_client.query_points.return_value = MagicMock(points=hits)
    module.qdrant_client.scroll.return_value = (document_points, None)
    docs = [{"id": doc_id, "index_status": "indexed"} for doc_id in {h.payload["doc_id"] for h in hits}]
    with (
        patch("app.rag_budget.retrieval_budget", return_value=budget),
        patch("modules.rag.get_user_documents", return_value=docs),
        patch("modules.rag.get_shared_document_ids", return_value={}),
    ):
        return module.search("user", "можно ли сейчас проводить ремонтные работы", file_coverage=True)


@pytest.mark.unit
def test_the_best_document_is_passed_whole_and_keeps_the_other_documents():
    module = _module()
    hits = [
        _hit("law", 7, "производстве ремонтных работ", 0.95, filename="law.txt"),
        _hit("cv", 0, "резюме", 0.6),
        _hit("other", 0, "другой документ", 0.5),
    ]
    points = [_point("law", 0, "статья 1"), _point("law", 1, "с 22 часов до 7 часов"), _point("law", 7, "ремонтные работы")]

    chunks, scores = _search(module, _budget(3000), hits, points)

    assert [chunk["doc_id"] for chunk in chunks] == ["law", "law", "law", "cv", "other"]
    assert [chunk["chunk_index"] for chunk in chunks[:3]] == [0, 1, 7]
    assert "с 22 часов до 7 часов" in chunks[1]["text"]
    assert scores[0] == scores[2] == 0.95, "every chunk of the document carries its hit's score"
    assert all("coverage" not in chunk for chunk in chunks[:3])


@pytest.mark.unit
def test_a_document_that_does_not_fit_the_budget_keeps_the_chunk_level_result():
    module = _module()
    hits = [_hit("law", 7, "ремонтные работы", 0.95, filename="law.txt")]
    points = [_point("law", index, "статья " + "текст " * 200) for index in range(5)]

    chunks, _scores = _search(module, _budget(100), hits, points)

    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 7


@pytest.mark.unit
def test_a_large_document_is_never_poured_into_the_prompt():
    module = _module()
    hits = [_hit("book", 0, "оглавление", 0.95)]
    # About 9 000 tokens — far above WHOLE_DOCUMENT_MAX_TOKENS, so the room is
    # not the reason to skip it.
    points = [_point("book", index, "очень длинный текст " * 300) for index in range(3)]

    chunks, _scores = _search(module, _budget(100_000), hits, points)

    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 0


@pytest.mark.unit
def test_coverage_that_no_longer_fits_is_reported_as_unrepresented():
    module = _module()
    hits = [
        _hit("law", 7, "ремонтные работы", 0.95),
        _hit("cv", 0, "резюме", 0.6),
        _hit("other", 0, "другой документ", 0.5),
    ]
    points = [_point("law", index, "статья " + "текст " * 100) for index in range(3)]
    full = sum(estimate_tokens(point.payload["text"], "reasoning", "ru") for point in points)

    chunks, _scores = _search(module, _budget(full), hits, points)

    assert [chunk["doc_id"] for chunk in chunks] == ["law", "law", "law"]
    assert module.last_coverage["covered"] == 0
    assert module.last_coverage["unrepresented"] == 2


@pytest.mark.unit
def test_a_chunk_of_the_same_document_is_not_duplicated():
    module = _module()
    hits = [
        _hit("law", 7, "ремонтные работы", 0.95),
        _hit("law", 0, "статья 1", 0.8),
    ]
    points = [_point("law", 0, "статья 1"), _point("law", 7, "ремонтные работы")]

    chunks, _scores = _search(module, _budget(3000), hits, points)

    assert [chunk["chunk_index"] for chunk in chunks] == [0, 7]
