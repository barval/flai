# tests/test_html_preview.py
"""Tests for the HTML preview endpoint.

The chat ▶ button used to open generated HTML via a blob: URL, which inherits
the strict chat CSP (script-src 'self') and blocks CDN imports (Three.js etc.)
— the preview tab was blank while the saved file worked. The endpoint serves
the message's HTML code block with its own relaxed CSP instead.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def authenticated_client(client, test_app):
    with test_app.app_context():
        from app.userdb import create_user, get_user_by_login

        if not get_user_by_login("htmlprev"):
            create_user("htmlprev", "pass123", "HTML Preview User")

    client.post("/login", data={"login": "htmlprev", "password": "pass123"})
    return client


HTML_BLOCK = "```html\n<!DOCTYPE html>\n<html><body><h1>Hi</h1></body></html>\n```"


@pytest.mark.unit
def test_preview_requires_login(client):
    response = client.get("/api/html-preview/123")
    assert response.status_code == 401


@pytest.mark.unit
def test_preview_rejects_missing_message(authenticated_client):
    with (
        patch("app.routes.messages.validate_session_ownership", return_value=True),
        patch("app.routes.messages.get_db") as mock_get_db,
    ):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = None
        mock_get_db.return_value.__enter__.return_value = conn
        response = authenticated_client.get("/api/html-preview/999")
    assert response.status_code == 404


@pytest.mark.unit
def test_preview_rejects_message_without_html(authenticated_client):
    with (
        patch("app.routes.messages.validate_session_ownership", return_value=True),
        patch("app.routes.messages.get_db") as mock_get_db,
    ):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = {"id": 1, "content": "just text, no code", "session_id": "s1"}
        mock_get_db.return_value.__enter__.return_value = conn
        response = authenticated_client.get("/api/html-preview/1")
    assert response.status_code == 404


@pytest.mark.unit
def test_preview_serves_html_with_relaxed_csp(authenticated_client):
    with (
        patch("app.routes.messages.validate_session_ownership", return_value=True),
        patch("app.routes.messages.get_db") as mock_get_db,
    ):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = {"id": 1, "content": HTML_BLOCK, "session_id": "s1"}
        mock_get_db.return_value.__enter__.return_value = conn
        response = authenticated_client.get("/api/html-preview/1")
    assert response.status_code == 200
    assert b"<!DOCTYPE html>" in response.data
    assert response.mimetype == "text/html"
    csp = response.headers.get("Content-Security-Policy", "")
    assert "cdn.jsdelivr.net" in csp
    assert "frame-ancestors 'none'" in csp
    # the global chat policy (default-src 'self') must NOT apply
    assert "default-src 'self'" not in csp
    assert "default-src 'none'" in csp


@pytest.mark.unit
def test_preview_ownership_required(authenticated_client):
    with patch("app.routes.messages.validate_session_ownership", return_value=False):
        response = authenticated_client.get("/api/html-preview/1")
    assert response.status_code == 404


@pytest.mark.unit
def test_handle_open_html_uses_preview_endpoint():
    src = Path("app/static/js/chat-messages.js").read_text(encoding="utf-8")
    assert "handleOpenHtmlClick" in src
    assert "/api/html-preview/" in src
    # the blob path must be gone
    assert "URL.createObjectURL" not in src
