# tests/test_reranker_module.py
"""The reranker is a first-class module: a config row, a card, its own switches."""

from pathlib import Path

import pytest


@pytest.mark.requires_db
def test_model_configs_has_a_reranker_row(test_app):
    """Seeded for new and upgraded installations alike."""
    with test_app.app_context():
        from app.model_config import reload_all_model_configs

        configs = reload_all_model_configs()
        reranker = configs.get("reranker") or {}
        assert reranker, "the reranker module must exist in model_configs"
        assert int(reranker.get("context_length") or 0) >= 512


@pytest.mark.unit
def test_validator_accepts_the_reranker_module():
    from app.validators import ValidationError, validate_model_config_update

    updates = validate_model_config_update({"model_name": "custom-cross-encoder", "context_length": 1024}, "reranker")
    assert updates["model_name"] == "custom-cross-encoder"

    with pytest.raises(ValidationError):
        validate_model_config_update({"model_name": "x"}, "not-a-module")


@pytest.mark.unit
def test_admin_renders_the_reranker_as_a_module_card():
    js = Path("app/static/js/admin-models.js").read_text(encoding="utf-8")
    assert "{ id: 'reranker', name: 'Reranker'" in js, "the card joins the module list"
    assert "module === 'reranker') return type === 'reranker'" in js, "only reranker files are offered"
    assert "type !== 'embedding' && type !== 'reranker'" in js, "chat cards stop offering rerankers"
    for key in ("RAG_RERANK_ENABLED", "RAG_RERANK_CANDIDATES", "RAG_RERANK_TIMEOUT"):
        assert key in js, f"the card saves {key}"
    assert "BGE Cross-Encoder" not in js, "invented specs are gone: details come from the GGUF header"

    html = Path("app/templates/admin.html").read_text(encoding="utf-8")
    assert 'id="reranker-card"' not in html, "the card is rendered like every other module"
    assert "window.RAG_RERANK_SETTINGS" in html, "the card needs the current values"


def test_the_card_labels_are_exposed_to_the_script():
    """The card is rendered by JS: t() reads window.TRANSLATIONS, not the catalog."""
    html = Path("app/templates/admin.html").read_text(encoding="utf-8")
    for label in ("Reranker", "Cross-encoder reranking", "Candidates to rerank"):
        assert f"'{label}': {{{{ _('{label}')|tojson }}}}" in html, f"{label} is not in TRANSLATIONS"


def test_the_card_inputs_use_the_chunks_row():
    """Equal, full-width fields — the same .chunks-config row as the Chunks card."""
    js = Path("app/static/js/admin-models.js").read_text(encoding="utf-8")
    start = js.index("if (mod.id === 'reranker')")
    block = js[start : js.index("if (mod.id !== 'embedding'", start)]
    assert '<div class="chunks-config">' in block
    assert '<div class="parameters">' not in block


def test_an_unset_reranker_adopts_the_legacy_file():
    """The card must open with a selected model, not an empty dropdown."""
    db = Path("app/database.py").read_text(encoding="utf-8")
    assert "module = 'reranker' AND (model_name IS NULL OR model_name = '')" in db


def test_the_hub_type_filter_offers_rerankers():
    """The Hub filter listed only reasoning/multimodal/embedding models."""
    html = Path("app/templates/admin.html").read_text(encoding="utf-8")
    assert 'id="hub-type-reranker"' in html

    js = Path("app/static/js/admin-modelHub.js").read_text(encoding="utf-8")
    assert "reranker: document.getElementById('hub-type-reranker')" in js
    assert "reranker: 'reranker'" in js, "a downloaded reranker must land in its own module"

    from app.model_hub import MODEL_TYPES, _module_for_type

    assert "reranker" in MODEL_TYPES
    assert _module_for_type("reranker") == "reranker"

    po = Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    assert 'msgid "Reranker models"\nmsgstr "Реранкеры"' in po
