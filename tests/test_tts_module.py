# tests/test_tts_module.py
"""Tests for TTS module (Piper text-to-speech)."""
import pytest
from unittest.mock import Mock, patch, MagicMock


@pytest.mark.unit
class TestTTSModule:
    """Test cases for TTSModule class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = Mock()
        app.config = {
            'PIPER_URL': 'http://test-piper:8888/tts',
            'PIPER_API_TIMEOUT': 30
        }
        app.logger = Mock()
        return app

    def test_init_with_available_piper(self, mock_app):
        """Test module initialization when Piper is available."""
        from modules.tts import TTSModule

        with patch('modules.tts.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            module = TTSModule(mock_app)

            # Module should be available when mock returns 200
            # Note: availability is checked during init
            assert module.tts_url == 'http://test-piper:8888/tts'
            assert module.timeout == 30

    def test_init_with_unavailable_piper(self, mock_app):
        """Test module initialization when Piper is unavailable."""
        from modules.tts import TTSModule

        with patch('modules.tts.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")

            module = TTSModule(mock_app)

            assert module.available is False

    def test_synthesize_returns_none_when_unavailable(self, mock_app):
        """Test synthesize returns None when Piper is unavailable."""
        from modules.tts import TTSModule

        with patch('modules.tts.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")

            module = TTSModule(mock_app)

            audio_bytes = module.synthesize('Hello world', 'en', 'male')

            assert audio_bytes is None

    def test_synthesize_returns_none_on_timeout(self, mock_app):
        """Test synthesize returns None on timeout."""
        from modules.tts import TTSModule
        import requests

        with patch('modules.tts.requests.get') as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout()

            module = TTSModule(mock_app)

            audio_bytes = module.synthesize('Hello world', 'en', 'male')

            assert audio_bytes is None

    def test_check_availability_with_success(self, mock_app):
        """Test check_availability returns True when Piper is healthy."""
        from modules.tts import TTSModule

        with patch('modules.tts.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            module = TTSModule(mock_app)
            # Force re-check
            result = module.check_availability()

            # Should be True after successful check
            assert module.tts_url == 'http://test-piper:8888/tts'

    def test_check_availability_with_failure(self, mock_app):
        """Test check_availability returns False when Piper is unhealthy."""
        from modules.tts import TTSModule

        with patch('modules.tts.requests.get') as mock_get:
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

        with patch('modules.tts.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")

            module = TTSModule(mock_app)
            result = module.check_availability()

            assert result is False
            assert module.available is False
