"""Tests for rendering the real templates without the application."""

import pytest

from demo.render import RenderError, render_app, render_login

CHAT_CONTEXT = {
    "sessions": [
        {
            "id": "s1",
            "title": "Weather",
            "updated_at": "2026-10-20T10:00:00",
            "message_count": 2,
            "response_style": "neutral",
        }
    ],
    "documents": [],
    "current_session": "s1",
    "embedding_model": "bge-m3",
}

ADMIN_CONTEXT = {
    "rooms": [],
    "camera_rooms": [],
    "camera_enabled": True,
    "chat_db_size": 1,
    "user_db_size": 1,
    "files_db_size": 1,
    "documents_db_size": 1,
    "chunk_size": 500,
    "chunk_overlap": 50,
    "chunk_strategy": "structured",
    "rag_top_k": 20,
    "max_top_k": 100,
    "rag_threshold_default": 0.3,
    "rag_threshold_reasoning": 0.3,
    "rag_rerank_enabled": False,
    "rag_rerank_candidates": 10,
    "rag_rerank_timeout": 10,
    "backend_type": "llama-swap",
    "llama_swap_url": "http://flai-llamaswap:8080",
}


def test_chat_page_renders():
    html = render_app("ru", "chat", CHAT_CONTEXT)
    assert 'id="chat-messages"' in html
    assert "/static/js/chat-init.js" in html


def test_admin_page_renders():
    html = render_app("en", "admin", ADMIN_CONTEXT)
    assert 'data-tab="hardware"' in html


def test_login_is_prefilled_and_locked():
    html = render_login("ru", "admin")
    assert 'value="admin"' in html
    assert 'id="login"' in html
    assert "disabled" in html
    assert 'data-demo-login="1"' in html


def test_login_is_translated():
    assert "Вход" in render_login("ru")
    assert "Sign in" in render_login("en")


def test_unknown_page_kind_raises():
    with pytest.raises(ValueError):
        render_app("ru", "settings", CHAT_CONTEXT)


def test_login_anchor_is_checked(monkeypatch):
    import demo.render as render_module

    monkeypatch.setattr(render_module, "LOGIN_FORM_ANCHOR", "<form-missing>")
    with pytest.raises(RenderError):
        render_login("ru")


def test_authenticated_session_renders_the_theme_switch():
    html = render_app("ru", "chat", {**CHAT_CONTEXT, "session": {"login": "user", "language": "ru"}})
    assert 'id="theme-toggle"' in html
