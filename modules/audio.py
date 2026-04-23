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
        self.whisper_api_url = app.config.get('WHISPER_API_URL', 'http://flai-whisper:9000/asr')
        self.timeout = app.config.get('WHISPER_API_TIMEOUT', 120)

        self.logger.info(f"Initializing AudioModule with Whisper URL: {self.whisper_api_url}")

        # Initial availability check with retries (Whisper may start slower than web app)
        max_retries = app.config.get('SERVICE_RETRY_ATTEMPTS', 5)
        retry_delay = app.config.get('SERVICE_RETRY_DELAY', 2)  # seconds

        for attempt in range(1, max_retries + 1):
            if self.check_availability():
                break
            if attempt < max_retries:
                self.logger.warning(f"Whisper API not ready (attempt {attempt}/{max_retries}), retrying in {retry_delay}s...")
                import time
                time.sleep(retry_delay)
            else:
                self.logger.warning(f"Whisper API not available after {max_retries} attempts")

        if self.available:
            self.logger.info(f"AudioModule initialized and available (Whisper API: {self.whisper_api_url}), timeout: {self.timeout}s")
        else:
            self.logger.warning(f"AudioModule initialized, but Whisper API unavailable ({self.whisper_api_url}). Will retry on each request.")

    def check_availability(self):
        """Check Whisper API availability"""
        if not self.whisper_api_url:
            self.logger.error("WHISPER_API_URL not configured")
            return False

        try:
            # Whisper ASR Webservice: try multiple endpoints
            base_url = self.whisper_api_url.replace('/asr', '').rstrip('/')
            
            # Try health endpoint first (faster)
            health_url = f"{base_url}/health"
            try:
                response = requests.get(health_url, timeout=3, allow_redirects=True)
                if response.status_code == 200:
                    self.logger.info(f"Whisper API health check passed: {health_url}")
                    self.available = True
                    return True
            except Exception:
                pass  # Health endpoint may not exist
            
            # Try root endpoint - may return 307 redirect which is OK
            response = requests.get(base_url, timeout=5, allow_redirects=True)
            # 200, 307, 404, 405 all indicate service is running
            if response.status_code in [200, 307, 404, 405]:
                self.logger.info(f"Whisper API is reachable at {base_url} (status: {response.status_code})")
                self.available = True
                return True
            # Check if we got redirected successfully
            elif response.history and response.history[-1].status_code == 307:
                self.logger.info(f"Whisper API redirected (service is running): {base_url}")
                self.available = True
                return True
            else:
                self.logger.warning(f"Whisper API returned unexpected status: {response.status_code}")
                self.available = False
                return False
                
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Cannot connect to Whisper API at {self.whisper_api_url} - service may not be running")
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout connecting to Whisper API at {self.whisper_api_url}")
        except Exception as e:
            self.logger.error(f"Error checking Whisper API at {self.whisper_api_url}: {str(e)}")

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
        # Always re-check availability on each request (service may have restarted)
        self.logger.info(f"Checking Whisper API availability before transcription... (current available={self.available})")
        was_available = self.available
        self.check_availability()
        if was_available != self.available:
            self.logger.info(f"Whisper API availability changed: {was_available} -> {self.available}")
        
        if not self.available:
            self.logger.error("Whisper API unavailable after re-check")
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