# tests/test_history.py
"""Unit tests for modules/history.py — conversation-history search."""

from unittest.mock import Mock, patch

import pytest

import modules.history as history_module
from modules.history import (
    HISTORY_DEFAULT_LIMIT,
    HISTORY_FRAGMENT_CHARS,
    HISTORY_MAX_LIMIT,
    _plain_text,
    _split_words,
    format_history_context,
    get_history_overview,
    search_history,
)


class TestSearchHistory:
    """SQL word-matching search."""

    def _rows(self, data):
        """Return a mocked cursor with fetchall payload."""
        cursor = Mock()
        cursor.fetchall.return_value = data
        conn = Mock()
        conn.cursor.return_value = cursor
        return conn, cursor

    def test_empty_query_returns_empty(self):
        assert search_history("user", "") == []

    def test_missing_user_returns_empty(self):
        assert search_history("", "question") == []

    def test_search_builds_ranked_russian_english_and_simple_fts_query(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            result = search_history("alice", "What did we discuss about Python code?")
        assert result == []
        sql, params = cursor.execute.call_args.args
        assert "cs.user_id = %s" in sql
        assert "to_tsvector('russian'" in sql
        assert "to_tsvector('english'" in sql
        assert "to_tsvector('simple'" in sql
        assert "plainto_tsquery('russian'" in sql
        assert "OR" in sql
        assert "translate(m.content || ' ' || coalesce(cs.title, ''), 'ёЁ', 'ее')" in sql
        assert "ORDER BY match_rank DESC" in sql
        assert any("python" in str(value).lower() for value in params)
        assert any("code" in str(value).lower() for value in params)
        assert params[-1] == HISTORY_DEFAULT_LIMIT

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("о чём мы с тобой общались за всё время", ["общались", "время"]),
            ("мы раньше обсуждали с тобой написание кода?", ["раньше", "обсуждали", "написание", "кода"]),
            ("What did we discuss about Python code?", ["discuss", "python", "code"]),
        ],
    )
    def test_extracts_terms_for_both_interface_languages(self, query, expected):
        assert _split_words(query) == expected

    def test_search_excludes_current_message_and_session(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            search_history("alice", "Python", exclude_message_id=42, exclude_session_id="active")
        sql, params = cursor.execute.call_args.args
        assert "m.id <> %s" in sql
        assert "id <> %s" in sql
        assert params[0:4] == (20000, "alice", 42, "active")

    def test_search_sql_placeholder_count_matches_bound_parameters(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            search_history("alice", "Python", exclude_message_id=42, exclude_session_id="active")
        sql, params = cursor.execute.call_args.args
        assert sql.count("%s") == len(params)

    def test_search_binds_content_limits_before_relevance_terms(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            search_history("alice", "Python", max_message_chars=12000)
        params = cursor.execute.call_args.args[1]
        assert params[:2] == (12000, "alice")
        assert 12000 in params
        assert params[-1] == HISTORY_DEFAULT_LIMIT

    def test_json_message_content_returns_only_text_parts(self):
        content = '[{"type":"text","text":"We discussed Python"},{"type":"image","file_data":"secret"}]'
        assert _plain_text(content) == "We discussed Python"

    def test_search_extracts_text_before_returning_fragments(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            search_history("alice", "Python")
        sql = cursor.execute.call_args.args[0]
        assert "m.content" in sql
        assert "translate(m.content || ' ' || coalesce(cs.title, ''), 'ёЁ', 'ее')" in sql


class TestHistoryOverview:
    def _rows(self, data):
        cursor = Mock()
        cursor.fetchall.return_value = data
        conn = Mock()
        conn.cursor.return_value = cursor
        return conn, cursor

    def test_selects_messages_across_sessions_and_excludes_current(self):
        conn, cursor = self._rows(
            [
                {
                    "id": 1,
                    "session_id": "solar-session",
                    "session_title": "Solar system project",
                    "role": "user",
                    "content": '[{"type":"text","text":"Create a 3D solar system"}]',
                    "timestamp": None,
                },
                {
                    "id": 2,
                    "session_id": "code-session",
                    "session_title": "Python automation",
                    "role": "user",
                    "content": '[{"type":"text","text":"Write a Python script"}]',
                    "timestamp": None,
                },
            ]
        )
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            overview_fn = getattr(history_module, "get_history_overview", None)
            assert callable(overview_fn)
            overview = overview_fn("alice", exclude_message_id=42)

        sql, params = cursor.execute.call_args.args
        assert "PARTITION BY cs.id" in sql
        assert "m.id <> %s" in sql
        assert 42 in params
        assert params[-2:] == (1, 1)
        assert "Solar system project" in overview
        assert "Python automation" in overview
        assert "Create a 3D solar system" in overview
        assert "Write a Python script" in overview

    def test_limits_each_session_and_excludes_current_session(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            get_history_overview("alice", exclude_session_id="active-session")
        sql, params = cursor.execute.call_args.args
        assert "id <> %s" in sql
        assert "active-session" in params

    def test_uses_stored_session_summary_when_available(self):
        conn, _ = self._rows(
            [
                {
                    "session_id": "session-1",
                    "session_title": "Python",
                    "summary": "Discussed Python scripts and HTML projects.",
                    "content": None,
                    "timestamp": None,
                },
                {
                    "session_id": "session-2",
                    "session_title": "No summary",
                    "summary": None,
                    "content": '[{"type":"text","text":"Fallback edge message"}]',
                    "timestamp": None,
                },
            ]
        )
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            overview = get_history_overview("alice")
        assert "Discussed Python scripts and HTML projects." in overview
        assert "Fallback edge message" in overview

    def test_limits_results_to_current_user(self):
        """Query always scopes by the current user's login."""
        conn, cursor = self._rows(
            [
                {
                    "id": 1,
                    "session_id": "s1",
                    "session_title": "Ремонт",
                    "role": "user",
                    "content": "обсуждали ремонт гостиной",
                    "timestamp": None,
                }
            ]
        )
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            result = search_history("alice", "ремонт")
        assert len(result) == 1
        assert "alice" in cursor.execute.call_args.args[1]

    def test_clamps_limit_to_max(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            search_history("alice", "word", limit=999)
        params = cursor.execute.call_args.args[1]
        assert params[-1] == HISTORY_MAX_LIMIT

    def test_clamps_negative_limit_to_min(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            search_history("alice", "word", limit=0)
        params = cursor.execute.call_args.args[1]
        assert params[-1] == 1

    def test_trims_fragment_text(self):
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = None
            conn = Mock()
            get_db.return_value.__enter__.return_value = conn
            conn.cursor.return_value.fetchall.return_value = [
                {
                    "id": 1,
                    "session_id": "s1",
                    "session_title": "T",
                    "role": "user",
                    "content": "x" * (HISTORY_FRAGMENT_CHARS + 50),
                    "timestamp": None,
                }
            ]
            result = search_history("alice", "word")
        assert len(result[0]["text"]) <= HISTORY_FRAGMENT_CHARS
        assert result[0]["text"].endswith("...")

    def test_db_error_returns_empty(self):
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.side_effect = RuntimeError("db down")
            assert search_history("alice", "word") == []


class TestFormatHistoryContext:
    def test_empty_returns_empty(self):
        assert format_history_context([]) == ""

    def test_formats_fragments_with_date_title_role(self):
        from datetime import datetime

        fragments = [
            {
                "session_title": "Ремонт",
                "role": "user",
                "text": "обсуждали ламинат",
                "timestamp": datetime(2026, 9, 10, 15, 30),
            },
            {
                "session_title": None,
                "role": "assistant",
                "text": "согласен",
                "timestamp": None,
            },
        ]
        out = format_history_context(fragments)
        assert "10.09.2026" in out
        assert "Ремонт" in out
        assert "(User)" in out
        assert "(Assistant)" in out
        assert out.index("[1.") < out.index("[2.")

    def test_honors_max_chars_by_dropping_trailing(self):
        fragments = []
        for _i in range(5):
            fragments.append(
                {
                    "session_title": "T",
                    "role": "user",
                    "text": "фрагмент" * 60,
                    "timestamp": None,
                }
            )
        full = format_history_context(fragments)
        assert len(full) > 2000
        limited = format_history_context(fragments, max_chars=1500)
        assert len(limited) <= 1500
        assert full.count("\n\n") >= limited.count("\n\n")
        assert len(limited) < len(full)


@pytest.mark.unit
def test_public_defaults_are_sane():
    assert 1 <= HISTORY_DEFAULT_LIMIT <= HISTORY_MAX_LIMIT
