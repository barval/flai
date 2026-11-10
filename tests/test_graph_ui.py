# tests/test_graph_ui.py
"""The graph view: markup, local assets and cache-busting."""

import re
from pathlib import Path

TEMPLATE = Path("app/templates/chat.html")
BASE = Path("app/templates/base.html")
SCRIPT = Path("app/static/js/chat-graph.js")
STYLE = Path("app/static/css/chat-graph.css")
VENDOR = Path("app/static/vendor/3d-force-graph.min.js")


def test_the_view_exists_and_is_hidden_by_default():
    html = TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r'<div[^>]*id="graph-view"[^>]*>', html)

    assert match, "graph-view markup is missing"
    assert "hidden" in match.group(0)


def test_nothing_is_loaded_from_a_cdn():
    for path in (SCRIPT, TEMPLATE):
        assert "https://" not in path.read_text(encoding="utf-8")


def test_the_assets_are_versioned_and_local():
    html = TEMPLATE.read_text(encoding="utf-8")
    base = BASE.read_text(encoding="utf-8")

    assert "js/chat-graph.js', v=config.STATIC_VERSION" in html
    assert "css/chat-graph.css', v=config.STATIC_VERSION" in base
    assert VENDOR.exists() and VENDOR.stat().st_size > 100_000
    assert "vendor/3d-force-graph.min.js" in SCRIPT.read_text(encoding="utf-8")
    assert STYLE.exists()


def test_the_view_offers_the_documented_entry_points():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "openGraphView" in script
    assert "closeGraphView" in script
    assert "/api/graph" in script
