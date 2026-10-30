"""The digest handed to the classifier must not speak the marker dialect to the kernel.

The documents line in ``build_router_context`` names the marker it wants
(``[-RAG-]``) because the marker router matches on substrings. The request kernel
never sees a marker and its prompt does not define one, so the same line is a
confusing token there. The digest is built one way for each consumer.
"""

from __future__ import annotations

import pytest

from modules.base import BaseModule


class _FakeRow(dict):
    pass


def _module(monkeypatch, documents=(3, ["contract.pdf", "report.pdf"])):
    module = BaseModule.__new__(BaseModule)
    module.logger = __import__("logging").getLogger("test")
    module.ROUTER_CONTEXT_MESSAGES = 6
    module.ROUTER_CONTEXT_MSG_CHARS = 240
    module.ROUTER_DOC_NAMES = 5
    module.ROUTER_SLM_FACTS = 0
    module.app = None

    import app.db as db

    monkeypatch.setattr(db, "get_session_recent_history", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(db, "get_user_document_names", lambda *a, **k: documents, raising=False)
    return module


@pytest.mark.unit
def test_marker_consumer_keeps_the_marker_hint(monkeypatch):
    module = _module(monkeypatch)
    digest = module.build_router_context("s", "user", "q", lang="en", marker_hints=True)
    assert "[-RAG-]" in digest
    assert "document search" in digest


@pytest.mark.unit
def test_kernel_consumer_gets_the_same_rule_without_the_marker(monkeypatch):
    module = _module(monkeypatch)
    digest = module.build_router_context("s", "user", "q", lang="en", marker_hints=False)
    assert "[-RAG-]" not in digest
    assert "document search" in digest
    assert "contract.pdf" in digest


@pytest.mark.unit
def test_russian_kernel_digest_drops_the_marker_too(monkeypatch):
    module = _module(monkeypatch)
    digest = module.build_router_context("s", "user", "q", lang="ru", marker_hints=False)
    assert "[-RAG-]" not in digest
    assert "поиск в документах" in digest
    assert "история переписки" in digest
