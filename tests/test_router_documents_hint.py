# tests/test_router_documents_hint.py
"""The router learns that the user has documents, and document extraction is RAG.

Measured on the live instance: a request to list every change that added an IP
address to the exclusions was classified as [-HISTORY-] and then [-REASONING-],
both of which skip retrieval, so the model answered from its weights while the
indexed files held every record. The prompt now names document extraction
explicitly and the router digest carries the user's library.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.db
from modules.base import BaseModule

pytestmark = pytest.mark.unit


def _module(names: int = 5) -> BaseModule:
    module = BaseModule.__new__(BaseModule)
    module.logger = MagicMock()
    module.ROUTER_DOC_NAMES = names
    module.ROUTER_SLM_FACTS = 0
    return module


def test_hint_lists_the_count_and_the_newest_names(monkeypatch):
    monkeypatch.setattr(app.db, "get_user_document_names", lambda user_id, limit=5: (42, ["works.txt", "hosting.pdf"]))

    context = _module().build_router_context("", "tester", "query", "ru")

    assert "Документы пользователя: 42 файлов" in context
    assert "works.txt, hosting.pdf" in context
    assert "[-RAG-]" in context


def test_hint_speaks_the_user_language(monkeypatch):
    monkeypatch.setattr(app.db, "get_user_document_names", lambda user_id, limit=5: (3, ["x.txt"]))

    context = _module().build_router_context("", "tester", "query", "en")

    assert "User's document library: 3 files" in context
    assert "[-RAG-]" in context


def test_no_hint_without_documents(monkeypatch):
    monkeypatch.setattr(app.db, "get_user_document_names", lambda user_id, limit=5: (0, []))

    assert _module().build_router_context("", "tester", "query", "ru") == ""


def test_guest_requests_do_not_query_the_library(monkeypatch):
    calls = []
    monkeypatch.setattr(app.db, "get_user_document_names", lambda *a, **k: calls.append(1) or (5, ["a.txt"]))

    assert _module().build_router_context("", None, "query", "ru") == ""
    assert calls == []


def test_a_failing_library_read_does_not_break_classification(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(app.db, "get_user_document_names", boom)

    assert _module().build_router_context("", "tester", "query", "ru") == ""


def test_templates_name_document_extraction_as_rag():
    ru = Path("prompts/ru/base_text.template").read_text(encoding="utf-8")
    en = Path("prompts/en/base_text.template").read_text(encoding="utf-8")

    assert "ИСКЛЮЧЕНИЕ для документов" in ru
    assert "выведи все работы" in ru
    assert "DOCUMENT EXCEPTION" in en
    assert "list all the work on" in en
    # Both templates keep the category-5 header the parser contract relies on.
    assert "## 5. ПОИСК В ДОКУМЕНТАХ — [-RAG-]" in ru
    assert "## 5. DOCUMENT SEARCH — [-RAG-]" in en


def test_attached_documents_are_named_for_this_message(monkeypatch):
    """The library line lists the newest five files, so an older document the
    user ticked is invisible to the classifier unless the attachment is named."""
    monkeypatch.setattr(app.db, "get_user_document_names", lambda user_id, limit=5: (96, ["latest.xlsx"]))

    context = _module().build_router_context(
        "", "tester", "Сколько лет Валерию Барсукову?", "ru", attached_docs=["CV.pdf"]
    )

    assert "К сообщению приложены документы: CV.pdf" in context
    assert "[-RAG-]" in context
    assert context.index("CV.pdf") > context.index("latest.xlsx"), "the attachment follows the library line"


def test_attached_documents_line_is_absent_without_attachments(monkeypatch):
    monkeypatch.setattr(app.db, "get_user_document_names", lambda user_id, limit=5: (96, ["latest.xlsx"]))

    assert "приложены документы" not in _module().build_router_context("", "tester", "query", "ru")


def test_attached_documents_line_speaks_the_user_language(monkeypatch):
    monkeypatch.setattr(app.db, "get_user_document_names", lambda user_id, limit=5: (1, ["a.pdf"]))

    context = _module().build_router_context("", "tester", "how old is he?", "en", attached_docs=["CV.pdf"])

    assert "Documents attached to this message: CV.pdf" in context


def test_at_most_five_attached_names_are_listed(monkeypatch):
    monkeypatch.setattr(app.db, "get_user_document_names", lambda user_id, limit=5: (10, ["a.pdf"]))

    context = _module().build_router_context(
        "", "tester", "query", "ru", attached_docs=[f"doc{i}.pdf" for i in range(8)]
    )

    assert "doc4.pdf" in context
    assert "doc5.pdf" not in context
