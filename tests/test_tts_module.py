"""Tests for TTS module (Kokoro TTS with Piper fallback)."""

from unittest.mock import MagicMock, Mock, patch

import pytest


@pytest.mark.unit
class TestTTSModule:
    """Test cases for TTSModule class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app with Kokoro URL."""
        app = Mock()
        app.config = {
            "KOKORO_URL": "http://test-kokoro:8888/tts",
            "KOKORO_TIMEOUT": 60,
            "PIPER_URL": None,
            "PIPER_TIMEOUT": 30,
        }
        app.logger = Mock()
        return app

    def test_init_with_available_kokoro(self, mock_app):
        """Test module initialization when Kokoro is available."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_head.return_value = mock_response

            module = TTSModule(mock_app)

            assert module.tts_url == "http://test-kokoro:8888/tts"
            assert module.backend == "kokoro"
            assert module.timeout == 60

    def test_init_with_unavailable_kokoro(self, mock_app):
        """Test module initialization when Kokoro is unavailable."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_head.side_effect = Exception("Connection error")

            module = TTSModule(mock_app)

            assert module.available is False

    def test_init_falls_back_to_piper(self, mock_app):
        """Test module falls back to Piper when Kokoro URL is not set."""
        mock_app.config = {
            "KOKORO_URL": None,
            "KOKORO_TIMEOUT": 60,
            "PIPER_URL": "http://test-piper:8888/tts",
            "PIPER_TIMEOUT": 30,
        }
        from modules.tts import TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_head.return_value = mock_response

            module = TTSModule(mock_app)

            assert module.backend == "piper"
            assert module.tts_url == "http://test-piper:8888/tts"

    def test_synthesize_returns_none_when_unavailable(self, mock_app):
        """Test synthesize returns None when TTS is unavailable."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_head.side_effect = Exception("Connection error")

            module = TTSModule(mock_app)

            audio_bytes, mime = module.synthesize("Hello world", "en", "male")

            assert audio_bytes is None and mime is None

    def test_synthesize_with_kokoro_backend(self, mock_app):
        """Test synthesize sends correct payload to Kokoro backend."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_head.return_value = mock_response

            module = TTSModule(mock_app)
            module.available = True

        with patch("modules.tts.requests.post") as mock_post:
            mock_audio = MagicMock()
            mock_audio.status_code = 200
            mock_audio.headers = {"content-type": "audio/wav"}
            mock_audio.content = b"fake_audio_data"
            mock_post.return_value = mock_audio

            from modules.tts import GENDER_VOICE_MAP

            result, mime = module.synthesize("Test text", "en", "female")

            # Check that post was called with voice name, not gender
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["voice"] == GENDER_VOICE_MAP[("en", "female")]
            assert call_kwargs["json"]["language"] == "en"
            assert result == b"fake_audio_data"

    def test_synthesize_with_piper_backend(self, mock_app):
        """Test synthesize sends correct payload to Piper backend."""
        mock_app.config = {
            "KOKORO_URL": None,
            "KOKORO_TIMEOUT": 60,
            "PIPER_URL": "http://test-piper:8888/tts",
            "PIPER_TIMEOUT": 30,
        }
        from modules.tts import TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_head.return_value = mock_response

            module = TTSModule(mock_app)
            module.available = True

        with patch("modules.tts.requests.post") as mock_post:
            mock_audio = MagicMock()
            mock_audio.status_code = 200
            mock_audio.headers = {"content-type": "audio/mpeg"}
            mock_audio.content = b"fake_mp3_data"
            mock_post.return_value = mock_audio

            result, mime = module.synthesize("Test text", "en", "male")

            # Piper backend uses gender, not voice
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["gender"] == "male"
            assert result == b"fake_mp3_data"

    def test_synthesize_returns_none_on_timeout(self, mock_app):
        """Test synthesize returns None on timeout."""
        import requests

        from modules.tts import TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_head.return_value = mock_response

            module = TTSModule(mock_app)
            module.available = True

        with patch("modules.tts.requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.Timeout()

            audio_bytes, mime = module.synthesize("Hello world", "en", "male")

            assert audio_bytes is None and mime is None

    def test_check_availability_with_success(self, mock_app):
        """Test check_availability returns True when Kokoro is healthy."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_head.return_value = mock_response

            module = TTSModule(mock_app)
            result = module.check_availability()

            assert result is True
            assert module.available is True

    def test_check_availability_with_failure(self, mock_app):
        """Test check_availability returns False when Kokoro is unhealthy."""
        from modules.tts import TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_head.return_value = mock_response

            module = TTSModule(mock_app)
            result = module.check_availability()

            assert result is False
            assert module.available is False

    def test_voice_resolution(self, mock_app):
        """Test voice name resolution from lang+gender."""
        from modules.tts import GENDER_VOICE_MAP, TTSModule

        with patch("modules.tts.requests.head") as mock_head:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_head.return_value = mock_response

            module = TTSModule(mock_app)

            # Default mappings
            assert GENDER_VOICE_MAP[("ru", "male")] == "dima"
            assert GENDER_VOICE_MAP[("ru", "female")] == "sveta"
            assert GENDER_VOICE_MAP[("en", "male")] == "am_liam"
            assert GENDER_VOICE_MAP[("en", "female")] == "af_heart"

            # Explicit voice overrides gender mapping
            assert module._resolve_voice("ru", "male", "masha") == "masha"


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
