import os
from dotenv import load_dotenv
import pytz
from pytz.exceptions import UnknownTimeZoneError

load_dotenv()

def load_config(app):
    """Загружает все переменные из .env в конфиг Flask."""
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    if not app.config['SECRET_KEY']:
        raise ValueError("SECRET_KEY must be set in .env file")

    app.config['JSON_AS_ASCII'] = False
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

    app.config['FOOTER_TEXT'] = os.getenv('FOOTER_TEXT')
    app.config['TIMEZONE_STR'] = os.getenv('TIMEZONE')
    app.config['OLLAMA_URL'] = os.getenv('OLLAMA_URL')
    app.config['REDIS_URL'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    app.config['LLM_CHAT_MODEL'] = os.getenv('LLM_CHAT_MODEL')
    app.config['LLM_CHAT_MODEL_CONTEXT_WINDOW'] = int(os.getenv('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768))
    app.config['LLM_CHAT_TEMPERATURE'] = float(os.getenv('LLM_CHAT_TEMPERATURE', 0.1))
    app.config['LLM_CHAT_TOP_P'] = float(os.getenv('LLM_CHAT_TOP_P', 0.1))
    app.config['LLM_MULTIMODAL_MODEL'] = os.getenv('LLM_MULTIMODAL_MODEL')
    app.config['LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW'] = int(os.getenv('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768))
    app.config['LLM_MULTIMODAL_TEMPERATURE'] = float(os.getenv('LLM_MULTIMODAL_TEMPERATURE', 0.7))
    app.config['LLM_MULTIMODAL_TOP_P'] = float(os.getenv('LLM_MULTIMODAL_TOP_P', 0.9))
    app.config['LLM_REASONING_MODEL'] = os.getenv('LLM_REASONING_MODEL')
    app.config['LLM_REASONING_MODEL_CONTEXT_WINDOW'] = int(os.getenv('LLM_REASONING_MODEL_CONTEXT_WINDOW', 40960))
    app.config['LLM_REASONING_TEMPERATURE'] = float(os.getenv('LLM_REASONING_TEMPERATURE', 0.7))
    app.config['LLM_REASONING_TOP_P'] = float(os.getenv('LLM_REASONING_TOP_P', 0.9))
    app.config['AUTOMATIC1111_URL'] = os.getenv('AUTOMATIC1111_URL')
    app.config['AUTOMATIC1111_MODEL'] = os.getenv('AUTOMATIC1111_MODEL')
    app.config['MAX_IMAGE_WIDTH'] = int(os.getenv('MAX_IMAGE_WIDTH', 3840))
    app.config['MAX_IMAGE_HEIGHT'] = int(os.getenv('MAX_IMAGE_HEIGHT', 2160))
    app.config['MAX_IMAGE_SIZE_MB'] = int(os.getenv('MAX_IMAGE_SIZE_MB', 5))
    app.config['WHISPER_API_URL'] = os.getenv('WHISPER_API_URL', 'http://host.docker.internal:9000/asr')
    app.config['LLM_CHAT_TIMEOUT'] = int(os.getenv('LLM_CHAT_TIMEOUT', 60))
    app.config['LLM_MULTIMODAL_TIMEOUT'] = int(os.getenv('LLM_MULTIMODAL_TIMEOUT', 120))
    app.config['LLM_REASONING_TIMEOUT'] = int(os.getenv('LLM_REASONING_TIMEOUT', 300))
    app.config['AUTOMATIC1111_TIMEOUT'] = int(os.getenv('AUTOMATIC1111_TIMEOUT', 180))
    app.config['WHISPER_API_TIMEOUT'] = int(os.getenv('WHISPER_API_TIMEOUT', 120))
    app.config['CAMERA_API_URL'] = os.getenv('CAMERA_API_URL', 'http://host.docker.internal:5005')
    app.config['CAMERA_API_TIMEOUT'] = int(os.getenv('CAMERA_API_TIMEOUT', 15))
    app.config['CAMERA_CHECK_INTERVAL'] = int(os.getenv('CAMERA_CHECK_INTERVAL', 30))

    # Настройка часового пояса
    if app.config['TIMEZONE_STR']:
        try:
            app.config['TIMEZONE'] = pytz.timezone(app.config['TIMEZONE_STR'])
            app.logger.info(f"Используется часовой пояс: {app.config['TIMEZONE_STR']}")
        except UnknownTimeZoneError:
            app.logger.error(f"Неизвестный часовой пояс '{app.config['TIMEZONE_STR']}'")
            app.config['TIMEZONE'] = None
    else:
        app.config['TIMEZONE'] = None
        app.logger.error("TIMEZONE не найден в .env файле")