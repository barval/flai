# modules/tts.py
import logging
import requests
from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

class TTSModule:
    """Module for text-to-speech synthesis via MeloTTS."""

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
        self.app = app
        self.tts_url = app.config.get('MELOTTS_URL')
        self.timeout = app.config.get('MELOTTS_TIMEOUT', 30)
        self.check_availability()
        if self.available:
            self.logger.info(f"TTSModule initialized and available (URL: {self.tts_url}), timeout: {self.timeout}s")
        else:
            self.logger.warning(f"TTSModule initialized, but MeloTTS unavailable ({self.tts_url})")

    def check_availability(self):
        if not self.tts_url:
            self.logger.error("MELOTTS_URL not configured")
            return False
        try:
            # Simple accessibility check (HEAD request)
            response = requests.head(self.tts_url, timeout=3)
            if response.status_code < 500:
                self.available = True
                return True
        except Exception as e:
            self.logger.error(f"Error checking MeloTTS availability: {str(e)}")
        self.available = False
        return False

    def synthesize(self, text, lang='ru'):
        """Generate speech audio bytes for given text."""
        if not self.available:
            self.logger.error("TTS unavailable")
            return None
        try:
            # We expect the MeloTTS service to accept a JSON POST with the text and language fields.
            payload = {'text': text, 'language': lang}
            self.logger.info(f"Sending TTS request for text (len={len(text)}) in {lang}")
            response = requests.post(self.tts_url, json=payload, timeout=self.timeout)
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