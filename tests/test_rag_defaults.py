# tests/test_rag_defaults.py
"""Default retrieval switches, chosen by measurement (see CHANGELOG v12.7)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import load_config

pytestmark = pytest.mark.unit


def test_hybrid_search_is_on_by_default(monkeypatch):
    """A 22-question set puts hybrid above dense-only on every metric:
    hit 0.909 vs 0.864, MRR 0.744 vs 0.688, nDCG 0.965 vs 0.751 — for the
    20-chunk chat window and for the 5-chunk task/tool window alike.
    """
    monkeypatch.delenv("RAG_HYBRID_ENABLED", raising=False)
    monkeypatch.setenv("SECRET_KEY", "test")

    app = SimpleNamespace(config={}, logger=MagicMock())
    load_config(app)

    assert app.config["RAG_HYBRID_ENABLED"] is True
