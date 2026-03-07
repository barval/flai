# modules/audio.py
import logging
import requests
import base64
import os
from datetime import datetime
from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

class AudioModule:
    """Module for audio transcription via Whisper API"""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.whisper_api_url = None
        self.available = False
        self.timeout = 120
        self.supported_audio_mimetypes = [
            'audio/webm', 'audio/wav', 'audio/mp3', 'audio/mpeg',
            'audio/ogg', 'audio/x-m4a', 'audio/x-wav', 'audio/aac'
        ]
        self.supported_video_mimetypes = [
            'video/mp4', 'video/x-msvideo', 'video/quicktime',
            'video/x-matroska', 'video/webm', 'video/ogg',
            'video/mpeg', 'video/3gpp', 'video/x-ms-wmv'
        ]
        self.supported_extensions = [
            '.webm', '.wav', '.mp3', '.ogg', '.m4a', '.aac',
            '.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv',
            '.m4v', '.3gp', '.mpg', '.mpeg'
        ]

        if app:
            self.init_app(app)

    def _(self, key, lang='ru', **kwargs):
        with self.app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)

    def init_app(self, app):
        """Initialize module with Flask app"""
        self.app = app
        self.whisper_api_url = app.config.get('WHISPER_API_URL', 'http://host.docker.internal:9000/asr')
        self.timeout = app.config.get('WHISPER_API_TIMEOUT', 120)
        self.check_availability()

        if self.available:
            self.logger.info(f"AudioModule initialized and available (Whisper API: {self.whisper_api_url}), timeout: {self.timeout}s")
        else:
            self.logger.warning(f"AudioModule initialized, but Whisper API unavailable ({self.whisper_api_url})")

    def check_availability(self):
        """Check Whisper API availability"""
        if not self.whisper_api_url:
            self.logger.error("WHISPER_API_URL not configured")
            return False

        try:
            response = requests.get(self.whisper_api_url, timeout=3)
            if response.status_code == 200:
                self.available = True
                return True
            else:
                self.available = True
                return True
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to Whisper API: {self.whisper_api_url}")
        except Exception as e:
            self.logger.error(f"Error checking Whisper API: {str(e)}")

        self.available = False
        return False

    def transcribe(self, audio_data, audio_format='audio/webm', filename='audio.webm', lang='ru'):
        """
        Transcribe audio via Whisper API
        audio_data: base64-encoded audio data
        audio_format: MIME type
        filename: original filename
        lang: language code (e.g., 'ru', 'en')
        Returns text or None on error
        """
        if not self.available:
            self.logger.error("Whisper API unavailable")
            return None

        try:
            audio_bytes = base64.b64decode(audio_data)
            files = {
                'audio_file': (filename, audio_bytes, audio_format)
            }
            params = {
                'output': 'json',
                'language': lang   # Pass language to Whisper API
            }

            self.logger.info(f"Sending file for transcription, size {len(audio_bytes)} bytes, "
                           f"filename: {filename}, format: {audio_format}, language: {lang}, timeout: {self.timeout}s")

            response = requests.post(
                self.whisper_api_url,
                files=files,
                params=params,
                timeout=self.timeout
            )

            self.logger.info(f"Whisper API response: status {response.status_code}")

            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                if 'application/json' in content_type or response.text.strip().startswith('{'):
                    try:
                        result = response.json()
                        text = result.get('text', '')
                        if text:
                            self.logger.info(f"Transcription successful (JSON): {text[:50]}...")
                            return text.strip()
                        else:
                            self.logger.error("Whisper API returned empty text in JSON")
                            return None
                    except Exception as e:
                        self.logger.error(f"JSON parsing error: {str(e)}")
                        text = response.text.strip()
                        if text:
                            self.logger.info(f"Transcription successful (plain text after JSON error): {text[:50]}...")
                            return text
                        else:
                            return None
                else:
                    text = response.text.strip()
                    if text:
                        self.logger.info(f"Transcription successful (plain text): {text[:50]}...")
                        return text
                    else:
                        self.logger.error("Whisper API returned empty response")
                        return None
            else:
                self.logger.error(f"Whisper API error: status {response.status_code}, response: {response.text[:200]}")
                return None

        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({self.timeout}s) during audio transcription")
            return None
        except requests.exceptions.ConnectionError:
            self.logger.error("Connection error to Whisper API")
            return None
        except Exception as e:
            self.logger.error(f"Error during transcription: {str(e)}")
            return None

    def is_audio_file(self, file_type, file_name):
        """Check if file is suitable for transcription."""
        if file_type:
            if file_type in self.supported_audio_mimetypes:
                return True
            if file_type in self.supported_video_mimetypes:
                return True
        if file_name:
            ext = os.path.splitext(file_name)[1].lower()
            if ext in self.supported_extensions:
                return True
        return False