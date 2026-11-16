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


def test_the_card_and_the_panel_header_open_the_view():
    documents = Path("app/static/js/chat-documents.js").read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'data-graph-doc="' in documents
    assert "documents-graph-button" in documents
    # One document-level listener survives every re-render of the list.
    assert "document.addEventListener('click'" in script
    assert "openGraphView" in script


def test_the_tooltip_exists_in_both_catalogs():
    assert "documents_view_in_graph" in Path("app/templates/chat.html").read_text(encoding="utf-8")
    for lang in ("en", "ru"):
        po = Path(f"translations/{lang}/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
        assert 'msgid "View in knowledge graph"' in po


def test_a_webgl_free_renderer_is_vendored():
    script = SCRIPT.read_text(encoding="utf-8")

    assert Path("app/static/vendor/force-graph.min.js").exists()
    assert "force-graph.min.js" in script
    # 3D is attempted for real and only then replaced by the 2D canvas.
    assert "createRenderer" in script and "window.__graphKind = '2d'" in script
    assert "graph-kind-btn" in Path("app/templates/chat.html").read_text(encoding="utf-8")


def test_a_bundle_already_present_is_not_fetched_again():
    """The published demo inlines the renderer; a lazy fetch of a file:// URL fails."""
    script = SCRIPT.read_text(encoding="utf-8")

    assert "typeof window[globalName] === 'function'" in script
    assert "globalName = kind === '3d' ? 'ForceGraph3D' : 'ForceGraph'" in script


def test_the_fallback_reason_stays_on_the_button():
    """The status line is replaced by the next message; the cause must survive."""
    script = SCRIPT.read_text(encoding="utf-8")

    assert "graph_3d_unavailable" in script
    assert "button.title = (kind === '3d'" in script
    assert "kind === '2d' && kindError" in script


def test_relation_types_can_be_hidden_and_the_choice_is_remembered():
    """Hiding a type is a view filter: the server answer is not fetched again."""
    html = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'id="graph-filters"' in html and 'id="graph-filter-list"' in html
    assert "hiddenPredicates" in script
    assert "flai.graph.hiddenPredicates" in script
    assert "function filteredGraph()" in script
    assert "graphInstance.graphData(graph);" in script


def test_a_highlighted_path_can_be_cleared():
    html = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'id="graph-clear-path"' in html
    assert "function resetColors()" in script
    assert "graphInstance.nodeColor(nodeColorFor)" in script


def test_the_storage_key_is_declared_before_the_first_read():
    """A `const` used by the initialiser above it is in its temporal dead zone."""
    script = SCRIPT.read_text(encoding="utf-8")

    assert script.index("const HIDDEN_PREDICATES_KEY") < script.index("new Set(loadHiddenPredicates())")


def test_a_node_can_be_merged_into_another_from_the_panel():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "graph_merge_start" in script and "graph-merge-btn" in script
    assert "mergeSourceId" in script
    assert "/api/graph/merge" in script
    # The merge never starts for a document somebody else shared.
    assert "node.shared" in script


def test_the_merge_route_refuses_a_foreign_node():
    routes = Path("app/routes/graph.py").read_text(encoding="utf-8")
    block = routes[routes.index('def graph_merge(') :]
    block = block[: block.index("@bp.route", 10)]

    assert "kg_nodes WHERE user_id = %s" in block
    assert "merge_kg_nodes(login, target, source)" in block
    assert '"error"' in block


def test_relation_types_are_named_for_a_reader_not_for_the_database():
    """`co-occurs`, `works for` and their Russian forms read as labels."""
    script = SCRIPT.read_text(encoding="utf-8")
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "function predicateLabel(" in script
    assert "graph_pred_co_occurs" in script and "graph_pred_works_for" in script
    assert "'graph_pred_co_occurs'" in html


def test_co_occurrence_is_hidden_until_the_reader_asks_for_it():
    """It is structural noise, not a stated relation, so it starts off."""
    script = SCRIPT.read_text(encoding="utf-8")

    assert "HIDDEN_BY_DEFAULT = ['co-occurs']" in script
    assert "FILTER_DEFAULTS_VERSION" in script
    # The stored list is honoured after the default has been applied once.
    assert "flai.graph.filterDefaults" in script


def test_a_structural_link_never_looks_like_a_stated_relation():
    """Shared chunks are drawn, but in their own colour and their own group."""
    script = SCRIPT.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "STRUCTURAL_PREDICATES = ['co-occurs']" in script
    assert "function structuralLinkColor()" in script
    assert "linkLineDash(linkDashFor)" in script
    assert "graph_relations_structural" in script
    assert ".graph-filter-dot-structural" in style
    assert ".graph-pred-structural" in style
