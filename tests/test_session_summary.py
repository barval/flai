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

    def test_summary_generated_when_history_trimmed(self, module, monkeypatch):
        monkeypatch.setattr("modules.base.get_session_summary", lambda sid: None)
        monkeypatch.setattr("modules.base.update_session_summary", lambda sid, s, u: None)
        monkeypatch.setattr(
            "app.db.get_session_messages",
            lambda sid, limit=100: [
                {"id": 1, "role": "user", "content": "first user message"},
                {"id": 2, "role": "assistant", "content": "first answer"},
                {"id": 3, "role": "user", "content": "second user message"},
            ],
        )
        module._summarize_session_history = MagicMock(return_value="COMPACT SUMMARY TEXT")

        section = module._get_session_summary_section("sid-1", oldest_kept_id=4, lang="ru")

        assert "COMPACT SUMMARY TEXT" in section
        assert "Кратко о предыдущем разговоре" in section
        module._summarize_session_history.assert_called_once()
        args = module._summarize_session_history.call_args
        assert args.args[0] == [
            {"id": 1, "role": "user", "content": "first user message"},
            {"id": 2, "role": "assistant", "content": "first answer"},
            {"id": 3, "role": "user", "content": "second user message"},
        ]

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
        assert args[1] == "qwen3vl"
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
