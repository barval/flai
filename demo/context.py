"""Build the template contexts of the demo pages from a captured snapshot."""

from __future__ import annotations

from typing import Any

from demo.snapshot import route_body


def _settings_values(snapshot: dict[str, Any]) -> dict[str, Any]:
    body = route_body(snapshot, "GET", "/admin/api/settings") or {}
    values: dict[str, Any] = {}
    for group in body.get("groups", []):
        for field in group.get("fields", []):
            values[field["key"]] = field.get("value")
    return values


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _showcase_session(sessions: list[dict[str, Any]]) -> str | None:
    """Open the conversation with the most messages, so the demo starts on a rich one."""
    if not sessions:
        return None
    best = max(sessions, key=lambda session: (session.get("message_count") or 0))
    return best["id"]


def chat_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    sessions = route_body(snapshot, "GET", "/api/sessions") or []
    documents = (route_body(snapshot, "GET", "/api/documents") or {}).get("documents", [])
    try:
        models = route_body(snapshot, "GET", "/admin/api/model_configs") or {}
    except KeyError:
        models = {}
    embedding = (models.get("embedding") or {}).get("model_name") or "bge-m3"
    return {
        "sessions": sessions,
        "documents": documents,
        "current_session": _showcase_session(sessions),
        "embedding_model": embedding,
    }


def admin_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    stats = route_body(snapshot, "GET", "/admin/api/stats") or {}
    models = route_body(snapshot, "GET", "/admin/api/model_configs") or {}
    settings = _settings_values(snapshot)
    cameras = route_body(snapshot, "GET", "/admin/api/cameras") or []

    chunk_size = _as_int(settings.get("RAG_CHUNK_SIZE"), 500)
    reasoning = models.get("reasoning") or {}
    context_length = _as_int(reasoning.get("context_length"), 8192)
    max_context_tokens = int(context_length * 0.30)
    max_top_k = max(1, int(max_context_tokens / (chunk_size / 3)))
    rag_top_k = min(_as_int(settings.get("RAG_TOP_K"), 20), max_top_k)

    return {
        "rooms": [],
        "camera_rooms": cameras,
        "camera_enabled": True,
        "chat_db_size": _as_int(stats.get("chat_db_size"), 0),
        "user_db_size": _as_int(stats.get("user_db_size"), 0),
        "files_db_size": _as_int(stats.get("files_db_size"), 0),
        "documents_db_size": _as_int(stats.get("documents_db_size"), 0),
        "chunk_size": chunk_size,
        "chunk_overlap": _as_int(settings.get("RAG_CHUNK_OVERLAP"), 50),
        "chunk_strategy": settings.get("RAG_CHUNK_STRATEGY") or "fixed",
        "rag_top_k": rag_top_k,
        "max_top_k": max_top_k,
        "rag_threshold_default": _as_float(settings.get("RAG_RELEVANCE_THRESHOLD_DEFAULT"), 0.3),
        "rag_threshold_reasoning": _as_float(settings.get("RAG_RELEVANCE_THRESHOLD_REASONING"), 0.3),
        "rag_rerank_enabled": _as_bool(settings.get("RAG_RERANK_ENABLED")),
        "rag_rerank_candidates": _as_int(settings.get("RAG_RERANK_CANDIDATES"), 10),
        "rag_rerank_timeout": _as_int(settings.get("RAG_RERANK_TIMEOUT"), 10),
        "backend_type": "llama-swap",
        "llama_swap_url": "http://flai-llamaswap:8080",
    }
