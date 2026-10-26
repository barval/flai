"""Tests for inlining the static assets and scrubbing server references."""

import pytest

from demo.inline import MissingAssetError, ScrubError, inline_page

HTML = (
    '<link rel="stylesheet" href="/static/css/chat.css?v=1">'
    '<script src="/static/js/chat-init.js?v=1"></script>'
    '<img src="/static/logo-header.png">'
)


def _fixture(tmp_path):
    (tmp_path / "app/static/css").mkdir(parents=True)
    (tmp_path / "app/static/js").mkdir(parents=True)
    (tmp_path / "app/static/logo-header.png").write_bytes(b"\x89PNG\r\n\x1a\nxx")
    (tmp_path / "app/static/css/chat.css").write_text("body{color:red}")
    (tmp_path / "app/static/js/chat-init.js").write_text("var a=1;")


def test_inlines_and_scrubs(tmp_path):
    _fixture(tmp_path)
    out = inline_page(HTML, tmp_path)
    assert "<style>body{color:red}</style>" in out
    assert "<script>var a=1;</script>" in out
    assert 'src="data:image/png;base64,' in out
    assert "/static/" not in out


def test_missing_asset_raises(tmp_path):
    (tmp_path / "app/static").mkdir(parents=True)
    with pytest.raises(MissingAssetError):
        inline_page('<script src="/static/js/nope.js"></script>', tmp_path)


def test_surviving_server_path_raises(tmp_path):
    (tmp_path / "app/static/js").mkdir(parents=True)
    (tmp_path / "app/static/js/a.js").write_text("var a=1;")
    with pytest.raises(ScrubError):
        inline_page('<source src="/static/media/clip.mp4"><script src="/static/js/a.js"></script>', tmp_path)
