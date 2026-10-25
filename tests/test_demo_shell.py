"""Tests for the offline page shell, the demo bar and the embedded documents."""

import json

from demo.shell import build_shell, escape_document

PAYLOAD = {
    "lang": "ru",
    "notice": "Это демонстрация интерфейса",
    "routes": [{"m": "GET", "p": "/api/sessions", "s": 200, "b": []}],
    "assets": {},
}


def test_escape_document_breaks_tags():
    assert escape_document("</script><!--") == "<\\/script><\\!--"


def test_shell_carries_both_documents_and_the_bar():
    html = build_shell("ru", "chat", login_html="<p>login</p>", app_html="<p>app</p>", payload=PAYLOAD)
    assert html.count('id="demo-login"') == 1
    assert html.count('id="demo-app"') == 1
    assert "demo-bar" in html
    assert 'data-device="mobile"' in html and 'data-device="desktop"' in html
    assert 'href="chat-en.html"' in html
    assert "<p>login</p>" in html and "<p>app</p>" in html
    assert "__DEMO__" in html
    json.loads(html.split("window.__DEMO__ = ", 1)[1].split(";\n", 1)[0])


def test_both_documents_install_the_shim():
    html = build_shell("en", "admin", login_html="<p>l</p>", app_html="<p>a</p>", payload=PAYLOAD)
    assert html.count("window.__DEMO__ = ") == 2
    assert 'href="admin-ru.html"' in html


def test_unknown_kind_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        build_shell("ru", "settings", login_html="", app_html="", payload=PAYLOAD)


def test_media_is_embedded_once():
    payload = dict(PAYLOAD, assets={"/api/files/a.png": "data:image/png;base64,AAAA"})
    html = build_shell("ru", "chat", login_html="<p>l</p>", app_html="<p>a</p>", payload=payload)
    assert html.count("data:image/png;base64,AAAA") == 1
