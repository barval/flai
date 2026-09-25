# tests/test_history.py
"""Unit tests for modules/history.py — conversation-history search."""

from unittest.mock import Mock, patch

import pytest

from modules.history import (
    HISTORY_DEFAULT_LIMIT,
    HISTORY_FRAGMENT_CHARS,
    HISTORY_MAX_LIMIT,
    format_history_context,
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

    def test_splits_words_and_executes_sql(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            result = search_history("alice", "ремонт гостиной")
        assert result == []
        sql = cursor.execute.call_args.args[0]
        params = cursor.execute.call_args.args[1]
        # user login filter + one ILIKE per word + LIMIT
        assert "cs.user_id = %s" in sql
        assert params[0] == "alice"
        assert "%ремонт%" in params and "%гостиной%" in params
        assert "ORDER BY m.timestamp DESC" in sql
        assert sql.rstrip().endswith("LIMIT %s")

    def test_limits_results_to_other_users(self):
        """Query always scopes by user login — rows belong to the caller."""
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
        # the login is always bound as the first parameter
        assert cursor.execute.call_args.args[1][0] == "alice"

    def test_clamps_limit_to_max(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            search_history("alice", "x", limit=999)
        params = cursor.execute.call_args.args[1]
        assert params[-1] == HISTORY_MAX_LIMIT

    def test_clamps_negative_limit_to_min(self):
        conn, cursor = self._rows([])
        with patch("modules.history.get_db") as get_db:
            get_db.return_value.__enter__.return_value = conn
            search_history("alice", "x", limit=0)
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
