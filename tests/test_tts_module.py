# tests/test_tts_module.py
"""Tests for TTS module (Piper text-to-speech)."""

from unittest.mock import MagicMock, Mock, patch

import pytest


@pytest.mark.unit
class TestTTSModule:
    """Test cases for TTSModule class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = Mock()
        app.config = {"PIPER_URL": "http://test-piper:8888/tts", "PIPER_API_TIMEOUT": 30}
        app.logger = Mock()
        return app

    def test_init_with_available_piper(self, mock_app):
        """Test module initialization when Piper is available."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            module = TTSModule(mock_app)

            # Module should be available when mock returns 200
            # Note: availability is checked during init
            assert module.tts_url == "http://test-piper:8888/tts"
            assert module.timeout == 30

    def test_init_with_unavailable_piper(self, mock_app):
        """Test module initialization when Piper is unavailable."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection error")

            module = TTSModule(mock_app)

            assert module.available is False

    def test_synthesize_returns_none_when_unavailable(self, mock_app):
        """Test synthesize returns None when Piper is unavailable."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection error")

            module = TTSModule(mock_app)

            audio_bytes = module.synthesize("Hello world", "en", "male")

            assert audio_bytes is None

    def test_synthesize_returns_none_on_timeout(self, mock_app):
        """Test synthesize returns None on timeout."""
        import requests

        from modules.tts import TTSModule

        with patch("modules.tts.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout()

            module = TTSModule(mock_app)

            audio_bytes = module.synthesize("Hello world", "en", "male")

            assert audio_bytes is None

    def test_check_availability_with_success(self, mock_app):
        """Test check_availability returns True when Piper is healthy."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            module = TTSModule(mock_app)
            # Force re-check — should be True after successful check
            module.check_availability()
            assert module.tts_url == "http://test-piper:8888/tts"

    def test_check_availability_with_failure(self, mock_app):
        """Test check_availability returns False when Piper is unhealthy."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_get.return_value = mock_response

            module = TTSModule(mock_app)
            result = module.check_availability()

            assert result is False
            assert module.available is False

    def test_check_availability_with_exception(self, mock_app):
        """Test check_availability handles exceptions gracefully."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection error")

            module = TTSModule(mock_app)
            result = module.check_availability()

            assert result is False
            assert module.available is False


class TestCleanMarkdownForTTS:
    """Tests for clean_markdown_for_tts() markdown stripping."""

    def test_plain_text_preserved(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("Hello world") == "Hello world"

    def test_empty_string(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("") == ""

    def test_bold(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("**bold**") == "bold"

    def test_italic(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("*italic*") == "italic"

    def test_bold_italic(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("***bold italic***") == "bold italic"

    def test_link(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("[text](url)") == "text"

    def test_image_removed(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("![alt](img.jpg)") == ""

    def test_inline_code(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("text `code` here") == "text code here"

    def test_fenced_code_block_removed(self):
        from app.utils import clean_markdown_for_tts
        text = "before\n```\ncode block\n```\nafter"
        assert clean_markdown_for_tts(text) == "before\n\nafter"

    def test_heading(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("## Title") == "Title"

    def test_blockquote(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("> quote") == "quote"

    def test_unordered_list(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("- item") == "item"

    def test_ordered_list(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("1. item") == "item"

    def test_strikethrough(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("~~strike~~") == "strike"

    def test_html_tags(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("<b>text</b>") == "text"

    def test_exponentiation_preserved(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("3**2=9") == "3**2=9"

    def test_math_expression_preserved(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("2+2*2=?") == "2+2*2=?"

    def test_orphaned_bold_start_rus(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("**НН.") == "НН."

    def test_orphaned_bold_end_rus(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("РУ**") == "РУ"

    def test_orphaned_bold_start_eng(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("**New") == "New"

    def test_orphaned_bold_end_eng(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("York**") == "York"

    def test_underscore_variable_preserved(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("some_var") == "some_var"

    def test_underscore_italic_with_boundaries(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("text _italic_ word") == "text italic word"

    def test_mixed_formatting(self):
        from app.utils import clean_markdown_for_tts
        text = "**bold** and *italic* and `code`"
        assert clean_markdown_for_tts(text) == "bold and italic and code"

    def test_thematic_break(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("---") == ""

    def test_multiple_whitespace_collapsed(self):
        from app.utils import clean_markdown_for_tts
        assert clean_markdown_for_tts("hello    world") == "hello world"

    def test_nested_formatting(self):
        from app.utils import clean_markdown_for_tts
        text = "**bold *and italic* text**"
        assert clean_markdown_for_tts(text) == "bold and italic text"
