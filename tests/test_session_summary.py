# tests/test_session_summary.py
"""Unit tests for the rolling session summary orchestration."""

from unittest.mock import MagicMock

import pytest

from modules.base import BaseModule


@pytest.mark.unit
class TestSessionSummary:
    @pytest.fixture
    def module(self):
        app = MagicMock()
        app.config = {
            "SESSION_SUMMARY_MIN_MESSAGES": 6,
            "SESSION_SUMMARY_MAX_FETCH": 120,
            "SESSION_SUMMARY_MAX_CHARS": 1500,
        }
        app.modules = {}
        m = BaseModule(app)
        m.summary_min_messages = 6
        m.summary_max_fetch = 120
        return m

    def test_trimmed_history_schedules_a_refresh_instead_of_summarizing(self, module, monkeypatch):
        """A trimmed history queues the refresh as idle work.

        Regenerating inside the request loaded the multimodal model and then the
        reasoning model — a second model swap on the critical path. The request
        returns the stored summary and the queue refreshes it between turns.
        """
        monkeypatch.setattr(
            "modules.base.get_session_summary",
            lambda sid: {"summary": "STORED SUMMARY", "summary_upto_id": 2},
        )
        module._summarize_session_history = MagicMock(return_value="SHOULD NOT RUN")
        queue = MagicMock()
        module.app.request_queue = queue

        section = module._get_session_summary_section("sid-1", oldest_kept_id=4, lang="ru")

        assert "STORED SUMMARY" in section
        module._summarize_session_history.assert_not_called()
        queue.add_session_summary_task.assert_called_once_with("sid-1", 4, "ru")

    def test_no_refresh_is_queued_when_the_summary_already_covers_the_trim(self, module, monkeypatch):
        monkeypatch.setattr(
            "modules.base.get_session_summary",
            lambda sid: {"summary": "COVERED", "summary_upto_id": 10},
        )
        queue = MagicMock()
        module.app.request_queue = queue

        section = module._get_session_summary_section("sid-1", oldest_kept_id=4, lang="ru")

        assert "COVERED" in section
        queue.add_session_summary_task.assert_not_called()

    def test_summary_not_regenerated_when_covered(self, module, monkeypatch):
        monkeypatch.setattr(
            "modules.base.get_session_summary", lambda sid: {"summary": "OLD SUMMARY", "summary_upto_id": 99}
        )
        module._summarize_session_history = MagicMock(return_value="NEW")

        section = module._get_session_summary_section("sid-2", oldest_kept_id=50, lang="ru")

        assert "OLD SUMMARY" in section
        module._summarize_session_history.assert_not_called()

    def test_no_summary_when_no_trimming(self, module, monkeypatch):
        monkeypatch.setattr("modules.base.get_session_summary", lambda sid: None)
        module._summarize_session_history = MagicMock(return_value="NEW")

        assert module._get_session_summary_section("sid-3", None, lang="ru") == ""
        module._summarize_session_history.assert_not_called()

    def test_fallback_to_previous_summary_on_failure(self, module, monkeypatch):
        monkeypatch.setattr("modules.base.get_session_summary", lambda sid: None)
        monkeypatch.setattr(
            "app.db.get_session_messages",
            lambda sid, limit=100: [{"id": 10, "role": "user", "content": "x"}],
        )
        module._summarize_session_history = MagicMock(return_value=None)

        section = module._get_session_summary_section("sid-4", oldest_kept_id=20, lang="en")
        assert section == ""

        monkeypatch.setattr("modules.base.get_session_summary", lambda sid: {"summary": "OLD EN", "summary_upto_id": 5})
        section = module._get_session_summary_section("sid-4", oldest_kept_id=20, lang="en")
        assert "OLD EN" in section
        assert "previous conversation" in section

    def test_section_formatting(self, module):
        ru = module._format_summary_section("S", "ru")
        assert "Кратко о предыдущем разговоре" in ru and "S" in ru
        en = module._format_summary_section("S", "en")
        assert "previous conversation" in en and "S" in en
        assert module._format_summary_section(None, "ru") == ""
        assert module._format_summary_section("", "ru") == ""

    def test_summarize_uses_template_and_model(self, module, monkeypatch):
        from app.utils import format_prompt

        module._get_model_config = MagicMock(return_value={"model_name": "qwen3vl"})
        module.llamacpp = MagicMock()
        module.llamacpp.chat.return_value = "  Compact summary.  "

        res = module._summarize_session_history(
            [{"role": "user", "content": "Привет"}], lang="ru", previous_summary=None
        )

        assert res == "Compact summary."
        args, kwargs = module.llamacpp.chat.call_args
        assert args[0][0]["role"] == "user"
        assert kwargs.get("model_type") == "multimodal"
        assert kwargs.get("lang") == "ru"
        assert kwargs.get("temperature") == 0.3
        expected = format_prompt(
            "summarize.template", {"session_messages": "User: Привет", "previous_summary": ""}, lang="ru"
        )
        assert expected is not None
        assert args[0][0]["content"] == expected

    def test_summarize_handles_dict_response(self, module):
        module._get_model_config = MagicMock(return_value={"model_name": "qwen3vl"})
        module.llamacpp = MagicMock()
        module.llamacpp.chat.return_value = {"content": "From dict"}

        res = module._summarize_session_history([{"role": "user", "content": "hi"}], lang="ru")
        assert res == "From dict"

    def test_summarize_rejects_error_strings(self, module):
        module._get_model_config = MagicMock(return_value={"model_name": "qwen3vl"})
        module.llamacpp = MagicMock()
        module.llamacpp.chat.return_value = "⚠️ Request too long, please simplify your request"

        assert module._summarize_session_history([{"role": "user", "content": "hi"}], lang="ru") is None
