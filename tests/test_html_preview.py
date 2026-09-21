# tests/test_html_preview.py
"""Tests for the HTML preview endpoint.

The chat ▶ button used to open generated HTML via a blob: URL, which inherits
the strict chat CSP (script-src 'self') and blocks CDN imports (Three.js etc.)
— the preview tab was blank while the saved file worked. The endpoint serves
the message's HTML code block with its own relaxed CSP instead.
"""

import json
import re
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

# Model output that skips the Three.js import map and imports addon modules by
# absolute unpkg URL — three r160 addons internally import the bare 'three'
# specifier, so without an import map the whole module graph fails to link.
BROKEN_THREE_BLOCK = (
    '```html\n<!DOCTYPE html>\n<html><head><meta charset="UTF-8"></head><body>'
    '<script type="module">\n'
    "import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';\n"
    "import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js';\n"
    "</script></body></html>\n```"
)

CANONICAL_THREE_HTML = (
    "<!DOCTYPE html>\n<html><head>"
    '<script type="importmap">{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js",'
    '"three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script>'
    '</head><body><script type="module">'
    "import * as THREE from 'three';\n"
    "import { OrbitControls } from 'three/addons/controls/OrbitControls.js';\n"
    "</script></body></html>"
)


@pytest.mark.unit
class TestEnsureThreeImportMap:
    """Server-side rescue for model output that skips the Three.js import map."""

    def _rescue(self, html: str) -> str:
        from app.routes.messages import _ensure_three_importmap

        return _ensure_three_importmap(html)

    def test_rewrites_unpkg_imports_and_injects_map(self):
        html = (
            '<!DOCTYPE html>\n<html><head><meta charset="UTF-8"></head><body>'
            '<script type="module">\n'
            "import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';\n"
            "import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js';\n"
            "</script></body></html>"
        )
        out = self._rescue(html)
        assert 'type="importmap"' in out
        assert "three@0.160.0/build/three.module.js" in out
        assert "from 'three'" in out
        assert "from 'three/addons/controls/OrbitControls.js'" in out
        assert "examples/jsm/controls/OrbitControls.js" not in out
        # import map must come before the first module script
        assert out.find('type="importmap"') < out.find('type="module"')

    def test_rewrites_jsdelivr_urls(self):
        html = (
            '<html><head></head><body><script type="module">\n'
            "import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.167.1/build/three.module.js';\n"
            "import { OrbitControls } from 'https://cdn.jsdelivr.net/npm/three@0.167.1/examples/jsm/controls/OrbitControls.js';\n"
            "</script></body></html>"
        )
        out = self._rescue(html)
        assert "three@0.167.1/build/three.module.js" in out
        assert "from 'three/addons/controls/OrbitControls.js'" in out
        assert "three@0.167.1/examples/jsm/controls" not in out

    def test_preserves_existing_import_map(self):
        assert self._rescue(CANONICAL_THREE_HTML) == CANONICAL_THREE_HTML

    def test_injects_map_for_bare_three_import_without_map(self):
        html = (
            '<html><head></head><body><script type="module">\n'
            "import * as THREE from 'three';\n"
            "</script></body></html>"
        )
        out = self._rescue(html)
        assert 'type="importmap"' in out
        assert "exactly one import map" not in out

    def test_injected_map_is_valid_json(self):
        """Regression: the injected map used an f-string where '}}' collapsed to
        a single literal '}' — Chrome then rejected the import map
        ("invalid JSON") and the whole module graph failed to link, blanking
        every rescued Three.js page."""
        for html in (
            '<html><head></head><body><script type="module">\n'
            "import * as THREE from 'https://unpkg.com/three@0.160.0/build/three.module.js';\n"
            "import { OrbitControls } from 'https://unpkg.com/three@0.160.0/examples/jsm/controls/OrbitControls.js';\n"
            "</script></body></html>",
            '<html><head></head><body><script type="module">\n'
            "import * as THREE from 'three';\n"
            "</script></body></html>",
        ):
            out = self._rescue(html)
            match = re.search(r'<script type="importmap">(.*?)</script>', out, re.DOTALL)
            assert match, "no import map was injected"
            parsed = json.loads(match.group(1))  # would raise if braces/escaping are wrong
            assert parsed["imports"]["three"].startswith("https://")
            assert parsed["imports"]["three/addons/"].endswith("/")

    def test_page_without_three_is_untouched(self):
        html = "<!DOCTYPE html>\n<html><body><h1>Hello</h1></body></html>"
        assert self._rescue(html) == html


@pytest.mark.unit
def test_preview_rescues_broken_three_page(authenticated_client):
    with (
        patch("app.routes.messages.validate_session_ownership", return_value=True),
        patch("app.routes.messages.get_db") as mock_get_db,
    ):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = {"id": 1, "content": BROKEN_THREE_BLOCK, "session_id": "s1"}
        mock_get_db.return_value.__enter__.return_value = conn
        response = authenticated_client.get("/api/html-preview/1")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'type="importmap"' in body
    assert "from 'three'" in body
    assert "three/addons/controls/OrbitControls.js" in body
    assert "examples/jsm/controls/OrbitControls.js" not in body


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
    # legacy UMD bundles (e.g. cdnjs three.js r126) self-execute via
    # Function()/eval — without 'unsafe-eval' they abort before THREE.Scene
    # is defined and the preview is blank
    assert "'unsafe-eval'" in csp
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
