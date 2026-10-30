"""The kernel path must produce the router's shape and fail open to the marker router."""

from __future__ import annotations

import pytest


def _base(app):
    module = app.modules["base"]
    module.KERNEL_ENABLED = True
    module._validate_final_prompt = lambda *args, **kwargs: None  # type: ignore[method-assign]
    return module


@pytest.mark.unit
def test_kernel_path_returns_the_router_shape(app):
    module = _base(app)
    module.call_llamacpp = lambda *args, **kwargs: (
        '{"intent": "rag", "query": "IP addresses", "sources": {"documents": true}, "confidence": 0.8}'
    )
    result = module.process_message("где мы добавляли IP адреса?", "2026-10-30", lang="ru")
    assert {key: result[key] for key in ("action", "query", "needs_reasoning")} == {
        "action": "rag",
        "query": "IP addresses",
        "needs_reasoning": False,
    }


@pytest.mark.unit
def test_kernel_path_carries_the_tool_selection(app):
    module = _base(app)
    module.call_llamacpp = lambda *args, **kwargs: (
        '{"intent": "rag", "query": "contract", "tools": ["rag_search", "calculator"], "confidence": 0.9}'
    )
    result = module.process_message("найди в договоре", "2026-10-30", lang="ru")
    assert result["tools"] == ("rag_search", "calculator")


@pytest.mark.unit
def test_malformed_kernel_reply_falls_back_to_the_marker_router(app):
    module = _base(app)
    replies = iter(["not json at all", "[-SEARCH-] weather"])

    module.call_llamacpp = lambda *args, **kwargs: next(replies)

    result = module.process_message("погода", "2026-10-30", lang="ru")
    assert result["action"] == "search"
    assert result["tools"] is None


@pytest.mark.unit
def test_kernel_disabled_keeps_the_marker_path(app):
    module = _base(app)
    module.KERNEL_ENABLED = False
    module.call_llamacpp = lambda *args, **kwargs: "[-RAG-] contract"
    assert module.process_message("contract", "2026-10-30")["action"] == "rag"


@pytest.mark.unit
def test_kernel_is_asked_once_with_the_schema_when_constrained(app):
    module = _base(app)
    module.KERNEL_CONSTRAINED = True
    seen: list[dict] = []

    def fake_call(messages, **kwargs):
        seen.append(kwargs)
        return '{"intent": "none", "query": "hi"}'

    module.call_llamacpp = fake_call
    module.process_message("hi", "2026-10-30", lang="en")
    assert len(seen) == 1
    assert seen[0]["model_type"] == "multimodal"
    assert seen[0]["temperature"] == 0
    assert seen[0]["response_format"] == {"type": "json_object"}
