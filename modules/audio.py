# modules/audio.py
import logging
import requests
import base64
import os
from datetime import datetime

class AudioModule:
    """Модуль для работы с аудио (транскрибация через Whisper API)"""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.whisper_api_url = None
        self.available = False
        self.timeout = 120  # Значение по умолчанию
        # Поддерживаемые аудио MIME-типы
        self.supported_audio_mimetypes = [
            'audio/webm', 'audio/wav', 'audio/mp3', 'audio/mpeg',
            'audio/ogg', 'audio/x-m4a', 'audio/x-wav', 'audio/aac'
        ]
        # Поддерживаемые видео MIME-типы (из них FFmpeg извлечёт аудио)
        self.supported_video_mimetypes = [
            'video/mp4', 'video/x-msvideo', 'video/quicktime',
            'video/x-matroska', 'video/webm', 'video/ogg',
            'video/mpeg', 'video/3gpp', 'video/x-ms-wmv'
        ]
        # Расширения файлов (для случаев, когда MIME-тип не определён или не точен)
        self.supported_extensions = [
            '.webm', '.wav', '.mp3', '.ogg', '.m4a', '.aac',      # аудио
            '.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv',       # видео
            '.m4v', '.3gp', '.mpg', '.mpeg'
        ]

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Инициализация модуля с приложением Flask"""
        self.whisper_api_url = app.config.get('WHISPER_API_URL', 'http://host.docker.internal:9000/asr')
        self.timeout = app.config.get('WHISPER_API_TIMEOUT', 120)
        self.check_availability()

        if self.available:
            self.logger.info(f"AudioModule инициализирован и доступен (Whisper API: {self.whisper_api_url}), таймаут: {self.timeout}с")
        else:
            self.logger.warning(f"AudioModule инициализирован, но Whisper API недоступен ({self.whisper_api_url})")

    def check_availability(self):
        """Проверка доступности Whisper API"""
        if not self.whisper_api_url:
            self.logger.error("WHISPER_API_URL не настроен")
            return False

        try:
            # Пробуем сделать простой GET-запрос (некоторые API отвечают)
            response = requests.get(self.whisper_api_url, timeout=3)
            if response.status_code == 200:
                self.available = True
                return True
            else:
                # Если ответ не 200, но сервер доступен – считаем доступным
                self.available = True
                return True
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Ошибка подключения к Whisper API: {self.whisper_api_url}")
        except Exception as e:
            self.logger.error(f"Ошибка при проверке Whisper API: {str(e)}")

        self.available = False
        return False

    def transcribe(self, audio_data, audio_format='audio/webm', filename='audio.webm'):
        """
        Транскрибация аудио через Whisper API с настраиваемым таймаутом
        audio_data: base64-encoded audio data
        audio_format: MIME-тип файла (может быть аудио или видео)
        filename: исходное имя файла (для отправки)
        Возвращает текст или None при ошибке
        """
        if not self.available:
            self.logger.error("Whisper API недоступен")
            return None

        try:
            # Декодируем base64 в бинарные данные
            audio_bytes = base64.b64decode(audio_data)

            # Используем переданное имя файла как есть
            # Whisper API может определять формат по расширению или содержимому
            files = {
                'audio_file': (filename, audio_bytes, audio_format)
            }

            # Добавляем параметр output=json для получения JSON-ответа
            params = {'output': 'json'}

            self.logger.info(f"Отправка файла на транскрибацию, размер {len(audio_bytes)} байт, "
                           f"имя: {filename}, формат: {audio_format}, таймаут: {self.timeout}с")

            response = requests.post(
                self.whisper_api_url,
                files=files,
                params=params,
                timeout=self.timeout  # Увеличен таймаут для больших видео
            )

            self.logger.info(f"Whisper API ответ: статус {response.status_code}")

            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')

                # Пытаемся распарсить JSON, если это JSON
                if 'application/json' in content_type or response.text.strip().startswith('{'):
                    try:
                        result = response.json()
                        text = result.get('text', '')
                        if text:
                            self.logger.info(f"Транскрибация успешна (JSON): {text[:50]}...")
                            return text.strip()
                        else:
                            self.logger.error("Whisper API вернул пустой текст в JSON")
                            return None
                    except Exception as e:
                        self.logger.error(f"Ошибка парсинга JSON: {str(e)}")
                        # Пробуем прочитать как текст
                        text = response.text.strip()
                        if text:
                            self.logger.info(f"Транскрибация успешна (plain text после ошибки JSON): {text[:50]}...")
                            return text
                        else:
                            return None
                else:
                    # Не JSON, считаем, что ответ - это просто текст
                    text = response.text.strip()
                    if text:
                        self.logger.info(f"Транскрибация успешна (plain text): {text[:50]}...")
                        return text
                    else:
                        self.logger.error("Whisper API вернул пустой ответ")
                        return None
            else:
                self.logger.error(f"Ошибка Whisper API: статус {response.status_code}, ответ: {response.text[:200]}")
                return None

        except requests.exceptions.Timeout:
            self.logger.error(f"Таймаут ({self.timeout}с) при транскрибации аудио")
            return None
        except requests.exceptions.ConnectionError:
            self.logger.error("Ошибка подключения к Whisper API")
            return None
        except Exception as e:
            self.logger.error(f"Ошибка при транскрибации: {str(e)}")
            return None

    def is_audio_file(self, file_type, file_name):
        """
        Проверка, является ли файл пригодным для отправки на транскрибацию.
        Возвращает True для аудио и видеофайлов, поддерживаемых Whisper API с FFmpeg.
        """
        # По MIME-типу
        if file_type:
            if file_type in self.supported_audio_mimetypes:
                return True
            if file_type in self.supported_video_mimetypes:
                return True
        # По расширению
        if file_name:
            ext = os.path.splitext(file_name)[1].lower()
            if ext in self.supported_extensions:
                return True
        return False