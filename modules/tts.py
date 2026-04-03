# modules/tts.py
import logging
import requests
from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

class TTSModule:
    """Module for text-to-speech synthesis via Piper TTS."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.tts_url = None
        self.available = False
        self.timeout = 30
        if app:
            self.init_app(app)

    def _(self, key, lang='ru', **kwargs):
        with self.app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)

    def init_app(self, app):
        """Initialize module with Flask app."""
        self.app = app
        self.tts_url = app.config.get('PIPER_URL')
        self.timeout = app.config.get('PIPER_TIMEOUT', 30)
        self.check_availability()
        if self.available:
            self.logger.info(f"TTSModule initialized and available (URL: {self.tts_url}), timeout: {self.timeout}s")
        else:
            self.logger.warning(f"TTSModule initialized, but Piper TTS unavailable ({self.tts_url})")

    def check_availability(self):
        """Check Piper TTS service availability."""
        if not self.tts_url:
            self.logger.error("PIPER_URL not configured")
            return False
        try:
            base_url = self.tts_url.replace('/tts', '')
            response = requests.head(f"{base_url}/health", timeout=3)
            if response.status_code == 200:
                self.available = True
                return True
        except Exception as e:
            self.logger.error(f"Error checking Piper TTS availability: {str(e)}")
        self.available = False
        return False

    def synthesize(self, text, lang='ru', gender='male'):
        """Generate speech audio bytes for given text and gender."""
        if not self.available:
            self.logger.error("TTS unavailable")
            return None
        try:
            payload = {
                'text': text,
                'language': lang,
                'gender': gender
            }
            t0 = __import__('time').time()
            self.logger.info(f"Sending TTS request for text (len={len(text)}) in {lang}, gender={gender}")
            response = requests.post(self.tts_url, json=payload, timeout=self.timeout)
            elapsed = __import__('time').time() - t0
            self.logger.info(f"Piper responded in {elapsed:.2f}s with status {response.status_code}")
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                if 'audio' in content_type:
                    return response.content  # bytes
                else:
                    self.logger.error(f"TTS returned non-audio content: {content_type}")
                    return None
            else:
                self.logger.error(f"TTS error: status {response.status_code}")
                return None
        except requests.exceptions.Timeout:
            self.logger.error(f"TTS timeout ({self.timeout}s)")
            return None
        except Exception as e:
            self.logger.error(f"Error during TTS synthesis: {str(e)}")
            return None