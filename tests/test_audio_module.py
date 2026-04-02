# tests/test_audio_module.py
"""Tests for Audio module (Whisper transcription)."""
import pytest
from unittest.mock import Mock, patch, MagicMock
import base64


@pytest.mark.unit
class TestAudioModule:
    """Test cases for AudioModule class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = Mock()
        app.config = {
            'WHISPER_API_URL': 'http://test-whisper:9000/asr',
            'WHISPER_API_TIMEOUT': 120
        }
        app.logger = Mock()
        return app

    def test_init_with_available_api(self, mock_app):
        """Test module initialization when API is available."""
        from modules.audio import AudioModule
        
        with patch('modules.audio.requests.get') as mock_get:
            mock_get.return_value.status_code = 200
            
            module = AudioModule(mock_app)
            
            assert module.available is True
            assert module.whisper_api_url == 'http://test-whisper:9000/asr'
            assert module.timeout == 120

    def test_init_with_unavailable_api(self, mock_app):
        """Test module initialization when API is unavailable."""
        from modules.audio import AudioModule
        
        with patch('modules.audio.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")
            
            module = AudioModule(mock_app)
            
            assert module.available is False

    def test_transcribe_returns_none_when_unavailable(self, mock_app):
        """Test that transcribe returns None when API is unavailable."""
        from modules.audio import AudioModule
        
        with patch('modules.audio.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")
            
            module = AudioModule(mock_app)
            
            # Should return None when unavailable
            result = module.transcribe('test_audio_data')
            assert result is None

    def test_is_audio_file_by_mime_type(self, mock_app):
        """Test is_audio_file checks MIME type correctly."""
        from modules.audio import AudioModule
        
        module = AudioModule(mock_app)
        
        # Test audio MIME types
        assert module.is_audio_file('audio/webm', 'test.webm') is True
        assert module.is_audio_file('audio/wav', 'test.wav') is True
        assert module.is_audio_file('audio/mp3', 'test.mp3') is True
        assert module.is_audio_file('audio/mpeg', 'test.mpeg') is True
        
        # Test video MIME types (also supported)
        assert module.is_audio_file('video/mp4', 'test.mp4') is True
        assert module.is_audio_file('video/webm', 'test.webm') is True
        
        # Test unsupported types
        assert module.is_audio_file('image/jpeg', 'test.jpg') is False
        assert module.is_audio_file('text/plain', 'test.txt') is False

    def test_is_audio_file_by_extension(self, mock_app):
        """Test is_audio_file checks file extension correctly."""
        from modules.audio import AudioModule
        
        module = AudioModule(mock_app)
        
        # Test supported extensions
        assert module.is_audio_file(None, 'test.webm') is True
        assert module.is_audio_file(None, 'test.wav') is True
        assert module.is_audio_file(None, 'test.mp3') is True
        assert module.is_audio_file(None, 'test.ogg') is True
        assert module.is_audio_file(None, 'test.m4a') is True
        assert module.is_audio_file(None, 'test.aac') is True
        
        # Test video extensions
        assert module.is_audio_file(None, 'test.mp4') is True
        assert module.is_audio_file(None, 'test.avi') is True
        assert module.is_audio_file(None, 'test.mkv') is True
        
        # Test unsupported extensions
        assert module.is_audio_file(None, 'test.jpg') is False
        assert module.is_audio_file(None, 'test.txt') is False

    def test_check_availability_with_health_endpoint(self, mock_app):
        """Test check_availability tries health endpoint first."""
        from modules.audio import AudioModule
        
        module = AudioModule(mock_app)
        
        with patch('modules.audio.requests.get') as mock_get:
            # Health endpoint succeeds
            mock_get.return_value.status_code = 200
            
            result = module.check_availability()
            
            assert result is True
            assert module.available is True
            # Should have tried health endpoint
            mock_get.assert_called()

    def test_check_availability_with_root_endpoint(self, mock_app):
        """Test check_availability falls back to root endpoint."""
        from modules.audio import AudioModule
        
        module = AudioModule(mock_app)
        
        with patch('modules.audio.requests.get') as mock_get:
            # Health endpoint fails, root endpoint succeeds
            mock_get.side_effect = [
                Exception("Health failed"),  # Health endpoint
                MagicMock(status_code=200)   # Root endpoint
            ]
            
            result = module.check_availability()
            
            assert result is True
            assert module.available is True
