# tests/test_chat_corpus.py
"""The chat corpus: owned doc ids reach the queue and scope the RAG search."""

import pytest

from app.queue import RedisRequestQueue
from app.routes.messages import _corpus_doc_ids

pytestmark = pytest.mark.unit


def test_corpus_keeps_only_owned_ids(monkeypatch):
    monkeypatch.setattr(
        "app.db.get_user_documents", lambda user_id: [{"id": "a"}, {"id": "b"}]
    )

    assert _corpus_doc_ids('["a", "foreign"]', "u") == ["a"]
    assert _corpus_doc_ids(["b", "foreign"], "u") == ["b"]
    assert _corpus_doc_ids("not-json", "u") == []
    assert _corpus_doc_ids(None, "u") == []


def test_rag_scope_merges_the_corpus_and_the_scope():
    assert RedisRequestQueue._rag_scope({}) is None
    assert RedisRequestQueue._rag_scope({"doc_ids": []}) is None
    assert RedisRequestQueue._rag_scope({"doc_ids": ["a"]}) == ["a"]
    assert RedisRequestQueue._rag_scope({"doc_ids": ["a"], "scope_docs": ["b"]}) == ["a", "b"]
    assert RedisRequestQueue._rag_scope({"doc_ids": ["a"], "scope_docs": []}) == ["a"]
