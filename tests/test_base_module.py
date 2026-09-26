# tests/test_base_module.py
"""Unit tests for base module."""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
class TestBaseModule:
    """Test base module functionality."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = MagicMock()
        app.config = {
            "OLLAMA_URL": "http://test:11434",
            "LLM_CHAT_MODEL": "test-model",
            "LLM_CHAT_TEMPERATURE": 0.7,
            "LLM_CHAT_TOP_P": 0.9,
            "LLM_CHAT_TIMEOUT": 300,
        }
        app.logger = MagicMock()
        app.modules = {}
        return app

    @pytest.fixture
    def camera_app(self, mock_app):
        """Mock app with an available CamModule (one room)."""
        fake_cam = MagicMock()
        fake_cam.available = True
        fake_cam.get_all_rooms_with_forms.return_value = [("kor", ["коридор"])]
        mock_app.modules["cam"] = fake_cam
        return mock_app

    @pytest.mark.unit
    def test_router_prompt_has_tense_principle(self, base_module):
        """Router prompt enforces the past-vs-now tense principle."""
        prompt = base_module._build_router_prompt("привет", "2026-09-26 10:00:00", lang="ru")
        assert "прошедш" in prompt
        assert "[-HISTORY-]" in prompt
        assert "КАТЕГОРИИ" in prompt or "категори" in prompt

    @pytest.mark.unit
    def test_router_prompt_has_tense_principle_en(self, base_module):
        """English router prompt enforces the past-vs-now tense principle."""
        prompt = base_module._build_router_prompt("hello", "2026-09-26 10:00:00", lang="en")
        assert "past tense" in prompt
        assert "[-HISTORY-]" in prompt

    @pytest.mark.unit
    def test_router_prompt_injects_recent_context(self, base_module):
        """Recent-context section appears in the router prompt."""
        ctx = "User: покажи коридор\nAssistant: Снимок с камеры"
        prompt = base_module._build_router_prompt(
            "мы смотрели снимки с камер?", "2026-09-26 10:00:00", lang="ru", recent_context=ctx
        )
        assert "покажи коридор" in prompt
        assert "Снимок с камеры" in prompt

    @pytest.mark.unit
    def test_router_prompt_empty_context_section(self, base_module):
        """Router prompt is usable with an empty recent context."""
        prompt = base_module._build_router_prompt("привет", "2026-09-26 10:00:00", lang="ru")
        assert prompt
        assert "Недавний контекст" in prompt

    @pytest.mark.unit
    def test_camera_section_excludes_past_tense(self, camera_app, base_module):
        """Camera classification explicitly excludes retrospective questions."""
        section = base_module._build_camera_prompt_section("ru")
        assert "ПРОШЛОМ" in section or "прошлом" in section
        assert "[-HISTORY-]" in section
        assert "СЕЙЧАС" in section

    @pytest.mark.unit
    def test_camera_section_excludes_past_tense_en(self, camera_app, base_module):
        """English camera section excludes retrospective questions."""
        section = base_module._build_camera_prompt_section("en")
        assert "PAST" in section
        assert "[-HISTORY-]" in section
        assert "NOW" in section

    @pytest.mark.unit
    def test_build_router_context_formats_history(self, base_module):
        """Router context digests recent messages into User/Assistant lines."""
        from unittest.mock import patch

        messages = [
            {"id": 1, "role": "user", "content": '[{"type": "text", "text": "покажи коридор"}]'},
            {"id": 2, "role": "assistant", "content": "Снимок с камеры: коридор"},
        ]
        with patch("app.db.get_session_recent_history", return_value=messages):
            ctx = base_module.build_router_context("s1", None, "мы смотрели снимки с камер?", "ru")
        assert "покажи коридор" in ctx
        assert "Снимок с камеры" in ctx
        assert "User:" in ctx
        assert "Assistant:" in ctx

    @pytest.mark.unit
    def test_build_router_context_excludes_current_message(self, base_module):
        """The current (just-sent) message is excluded via the DB helper."""
        from unittest.mock import patch

        messages = [
            {"id": 1, "role": "user", "content": "покажи коридор"},
            {"id": 2, "role": "assistant", "content": "Снимок с камеры"},
        ]
        with patch("app.db.get_session_recent_history", return_value=messages) as m:
            ctx = base_module.build_router_context(
                "s1", None, "мы смотрели снимки с камер?", "ru", exclude_message_id=3
            )
        assert "мы смотрели снимки с камер?" not in ctx
        assert "покажи коридор" in ctx
        assert m.call_args.kwargs["exclude_message_id"] == 3

    @pytest.mark.unit
    def test_build_router_context_slm_facts(self, mock_app):
        """SLM facts join the router context when the module is available."""
        from modules.base import BaseModule

        fake_slm = MagicMock()
        fake_slm.recall.return_value = [
            {"content": "пользователь смотрел снимки с камер 26.09"},
            {"content": "пользователь обсуждал курсы валют"},
        ]
        mock_app.modules["slm"] = fake_slm
        module = BaseModule(mock_app)
        from unittest.mock import patch

        with patch("app.db.get_session_recent_history", return_value=[]):
            ctx = module.build_router_context("s1", "valery", "мы смотрели снимки с камер?", "ru")
        assert "смотрел снимки с камер" in ctx
        fake_slm.recall.assert_called_once_with(
            "мы смотрели снимки с камер?", limit=module.ROUTER_SLM_FACTS, profile="valery"
        )

    @pytest.mark.unit
    def test_build_router_context_empty_session(self, base_module):
        """Empty session yields an empty context (no crash)."""
        from unittest.mock import patch

        with patch("app.db.get_session_recent_history", return_value=[]):
            ctx = base_module.build_router_context("", None, "привет", "ru")
        assert ctx == ""

    @pytest.fixture
    def base_module(self, mock_app):
        """Create base module instance."""
        from modules.base import BaseModule

        module = BaseModule(mock_app)
        return module

    @pytest.mark.unit
    def test_parse_router_response_no_marker(self, base_module):
        """Test parsing response without markers — returns original_query."""
        result = base_module._parse_router_response("any router output", "user query", "")
        assert result["action"] == "none"
        assert result["query"] == "user query"

    @pytest.mark.unit
    def test_parse_router_response_image_marker(self, base_module):
        """Test parsing response with image marker."""
        response = "[-IMAGE-] draw a cat"
        result = base_module._parse_router_response(response, "draw a cat", "")
        assert result["action"] == "image"
        assert result["query"] == "draw a cat"

    @pytest.mark.unit
    def test_parse_router_response_reasoning_marker(self, base_module):
        """Test parsing response with reasoning marker."""
        response = "[-REASONING-] solve this problem"
        result = base_module._parse_router_response(response, "", "")
        assert result["action"] == "reasoning"
        assert result["query"] == "solve this problem"

    @pytest.mark.unit
    def test_parse_router_response_reasoning_web_marker(self, base_module):
        """Test parsing response with reasoning-web marker."""
        response = "[-REASONING-WEB-] analyze current inflation data"
        result = base_module._parse_router_response(response, "", "")
        assert result["action"] == "reasoning_web"
        assert result["query"] == "analyze current inflation data"

    @pytest.mark.unit
    def test_parse_router_response_reasoning_web_priority(self, base_module):
        """reasoning-web marker wins when both reasoning markers are present."""
        response = "[-REASONING-WEB-] analyze current inflation data [-REASONING-] fallback"
        result = base_module._parse_router_response(response, "", "")
        assert result["action"] == "reasoning_web"

    @pytest.mark.unit
    def test_parse_router_response_camera_marker(self, base_module):
        """Test parsing response with camera marker."""
        response = "[-CAMERA-] show kitchen"
        result = base_module._parse_router_response(response, "", "")
        assert result["action"] == "camera"
        assert result["query"] == "show kitchen"

    @pytest.mark.unit
    def test_parse_router_response_rag_marker(self, base_module):
        """Test parsing response with RAG marker."""
        response = "[-RAG-] search documents"
        result = base_module._parse_router_response(response, "", "")
        assert result["action"] == "rag"
        assert result["query"] == "search documents"

    @pytest.mark.unit
    def test_parse_router_response_history_marker(self, base_module):
        """Test parsing response with history-search marker."""
        response = "[-HISTORY-] когда мы обсуждали ламинат"
        result = base_module._parse_router_response(response, "", "")
        assert result["action"] == "history"
        assert result["query"] == "когда мы обсуждали ламинат"

    @pytest.mark.unit
    def test_parse_router_response_none(self, base_module):
        """Test parsing None response."""
        result = base_module._parse_router_response(None, "", "")
        assert result["action"] == "none"
        assert result["query"] == ""

    @pytest.mark.integration
    def test_get_model_config_returns_config(self, test_app):
        """Test getting model configuration."""
        with test_app.app_context():
            from modules.base import BaseModule

            module = BaseModule(test_app)
            config = module._get_model_config("multimodal")

            assert config is not None
            assert "model_name" in config

    @pytest.mark.unit
    def test_context_history_section_heading(self, base_module):
        """History search fragments get a dedicated 'from your conversations' heading."""
        from unittest.mock import patch

        base_module._get_model_config = lambda model_type="multimodal": {"context_length": 10000}
        base_module._estimate_tokens = lambda text, model_type="multimodal", lang="ru": max(1, len(text or "") // 4)

        with patch(
            "modules.base.get_session_text_history", return_value=([], {"dropped_count": 0, "oldest_kept_id": None})
        ):
            ctx = base_module._get_context_for_model(
                "s1",
                "reasoning",
                "как выбирали ламинат",
                rag_context='[1. 10.09.2026 - "Ремонт" (User)]:\nобсуждали ламинат и подложку',
                rag_source="history",
            )
        assert "Найденная информация из вашей переписки:" in ctx
        assert "ламинат" in ctx

    @pytest.mark.unit
    def test_context_history_section_heading_en(self, base_module):
        """English heading for history fragments."""
        from unittest.mock import patch

        base_module._get_model_config = lambda model_type="multimodal": {"context_length": 10000}
        base_module._estimate_tokens = lambda text, model_type="multimodal", lang="ru": max(1, len(text or "") // 4)

        with patch(
            "modules.base.get_session_text_history", return_value=([], {"dropped_count": 0, "oldest_kept_id": None})
        ):
            ctx = base_module._get_context_for_model(
                "s1",
                "reasoning",
                "how we chose laminate",
                lang="en",
                rag_context='[1. 10.09.2026 - "Renov" (User)]:\nwe picked laminate and underlay',
                rag_source="history",
            )
        assert "Found information from your conversation history:" in ctx

    @pytest.mark.unit
    def test_context_web_search_heading_unchanged(self, base_module):
        """web_search keeps its own heading (regression guard after rename)."""
        base_module._get_model_config = lambda model_type="multimodal": {"context_length": 10000}
        base_module._estimate_tokens = lambda text, model_type="multimodal", lang="ru": max(1, len(text or "") // 4)

        from unittest.mock import patch

        with patch(
            "modules.base.get_session_text_history", return_value=([], {"dropped_count": 0, "oldest_kept_id": None})
        ):
            ctx = base_module._get_context_for_model(
                "s1",
                "reasoning",
                "current inflation",
                lang="en",
                rag_context="headline content",
                rag_source="web_search",
            )
        assert "Web search results" in ctx
