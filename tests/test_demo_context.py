"""Tests for building the template contexts from a snapshot."""

from demo.context import admin_context, chat_context

SNAPSHOT = {
    "routes": [
        {"method": "GET", "path": "/api/sessions", "body": [{"id": "s1", "title": "Weather"}]},
        {"method": "GET", "path": "/api/documents", "body": {"folders": [], "documents": [{"id": "d1"}]}},
        {
            "method": "GET",
            "path": "/admin/api/model_configs",
            "body": {"embedding": {"model_name": "bge-m3"}, "reasoning": {"context_length": 24576}},
        },
        {
            "method": "GET",
            "path": "/admin/api/stats",
            "body": {
                "chat_db_size": 10,
                "user_db_size": 20,
                "files_db_size": 30,
                "documents_db_size": 40,
            },
        },
        {
            "method": "GET",
            "path": "/admin/api/settings",
            "body": {
                "groups": [
                    {
                        "id": "rag",
                        "label": "Documents and RAG",
                        "fields": [
                            {"key": "RAG_CHUNK_SIZE", "value": 500},
                            {"key": "RAG_CHUNK_OVERLAP", "value": 50},
                            {"key": "RAG_CHUNK_STRATEGY", "value": "structured"},
                            {"key": "RAG_TOP_K", "value": 20},
                            {"key": "RAG_RELEVANCE_THRESHOLD_DEFAULT", "value": 0.3},
                            {"key": "RAG_RELEVANCE_THRESHOLD_REASONING", "value": 0.35},
                            {"key": "RAG_RERANK_ENABLED", "value": False},
                            {"key": "RAG_RERANK_CANDIDATES", "value": 10},
                            {"key": "RAG_RERANK_TIMEOUT", "value": 10},
                        ],
                    }
                ]
            },
        },
        {"method": "GET", "path": "/admin/api/cameras", "body": [{"code": "cam1", "room": "Hall"}]},
    ],
}


def test_chat_context_uses_sessions_and_documents():
    context = chat_context(SNAPSHOT)
    assert context["sessions"] == [{"id": "s1", "title": "Weather"}]
    assert context["documents"] == [{"id": "d1"}]
    assert context["current_session"] == "s1"
    assert context["embedding_model"] == "bge-m3"


def test_admin_context_computes_max_top_k():
    context = admin_context(SNAPSHOT)
    assert context["chunk_size"] == 500
    assert context["chunk_strategy"] == "structured"
    assert context["max_top_k"] == 44
    assert context["rag_top_k"] == 20


def test_admin_context_reads_sizes_and_thresholds():
    context = admin_context(SNAPSHOT)
    assert context["chat_db_size"] == 10
    assert context["documents_db_size"] == 40
    assert context["rag_threshold_default"] == 0.3
    assert context["rag_threshold_reasoning"] == 0.35
    assert context["rag_rerank_enabled"] is False
    assert context["camera_rooms"] == [{"code": "cam1", "room": "Hall"}]


def test_admin_context_clamps_rag_top_k():
    snapshot = {
        "routes": [
            {"method": "GET", "path": "/admin/api/stats", "body": {}},
            {"method": "GET", "path": "/admin/api/cameras", "body": []},
            {
                "method": "GET",
                "path": "/admin/api/model_configs",
                "body": {"reasoning": {"context_length": 8192}},
            },
            {
                "method": "GET",
                "path": "/admin/api/settings",
                "body": {
                    "groups": [
                        {
                            "id": "rag",
                            "label": "RAG",
                            "fields": [
                                {"key": "RAG_CHUNK_SIZE", "value": 500},
                                {"key": "RAG_TOP_K", "value": 100},
                            ],
                        }
                    ]
                },
            },
        ]
    }
    context = admin_context(snapshot)
    assert context["max_top_k"] == 14
    assert context["rag_top_k"] == 14
