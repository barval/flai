import logging
import time

import requests

from app.mixins import TranslationMixin
from app.utils import clean_markdown_for_tts

GENDER_VOICE_MAP = {
    ("ru", "male"): "dima",
    ("ru", "female"): "sveta",
    ("en", "male"): "am_liam",
    ("en", "female"): "af_heart",
}


class TTSModule(TranslationMixin):
    """Module for text-to-speech synthesis via Kokoro TTS (primary) or Piper TTS (fallback)."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.tts_url = None
        self.backend = None
        self.available = False
        self.timeout = 30
        if app:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        # Try Kokoro first, fallback to Piper
        self.tts_url = app.config.get("KOKORO_URL") or app.config.get("PIPER_URL")
        self.backend = "kokoro" if app.config.get("KOKORO_URL") else ("piper" if app.config.get("PIPER_URL") else None)
        self.timeout = app.config.get("KOKORO_TIMEOUT") or app.config.get("PIPER_TIMEOUT", 30)
        self.check_availability()
        if self.available:
            self.logger.info(
                f"TTSModule initialized ({self.backend} backend, URL: {self.tts_url}), timeout: {self.timeout}s"
            )
        else:
            self.logger.warning(f"TTSModule initialized, but TTS unavailable ({self.tts_url})")

    def check_availability(self):
        if not self.tts_url:
            self.logger.error("TTS URL not configured")
            return False
        try:
            base_url = self.tts_url.replace("/tts", "")
            response = requests.head(f"{base_url}/health", timeout=3)
            if response.status_code == 200:
                self.available = True
                return True
        except Exception as e:
            self.logger.error(f"Error checking TTS availability: {str(e)}")
        self.available = False
        return False

    def _resolve_voice(self, lang, gender, voice):
        if voice:
            return voice
        return GENDER_VOICE_MAP.get((lang, gender), GENDER_VOICE_MAP.get(("en", "male")))

    def synthesize(self, text, lang="ru", gender="male", voice=None):
        if self.tts_url and not self.available:
            self.check_availability()
        if not self.available:
            self.logger.error("TTS unavailable")
            return None, None
        try:
            raw = text
            text = clean_markdown_for_tts(text)
            if "**" in raw or "**" in text:
                self.logger.info(f"TTS markdown: {raw!r} -> {text!r}")

            if self.backend == "kokoro":
                voice_name = self._resolve_voice(lang, gender, voice)
                payload = {"text": text, "language": lang, "voice": voice_name}
            else:
                payload = {"text": text, "language": lang, "gender": gender}

            t0 = time.time()
            self.logger.info(f"Sending TTS request ({self.backend}) for text (len={len(text)}) in {lang}")
            response = requests.post(self.tts_url, json=payload, timeout=self.timeout)
            elapsed = time.time() - t0
            self.logger.info(f"TTS ({self.backend}) responded in {elapsed:.2f}s with status {response.status_code}")
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "audio/mpeg")
                if "audio" in content_type:
                    return response.content, content_type
                else:
                    self.logger.error(f"TTS returned non-audio content: {content_type}")
                    return None, None
            else:
                self.logger.error(f"TTS error: status {response.status_code}")
                return None, None
        except requests.exceptions.Timeout:
            self.logger.error(f"TTS timeout ({self.timeout}s)")
            return None, None
        except Exception as e:
            self.logger.error(f"Error during TTS synthesis: {str(e)}")
            return None, None
