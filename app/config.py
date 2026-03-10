# app/config.py
# Configuration loader for FLAI application

import os
from dotenv import load_dotenv
import pytz
from pytz.exceptions import UnknownTimeZoneError

load_dotenv()


def load_config(app):
    """Load all variables from .env into Flask config."""
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    if not app.config['SECRET_KEY']:
        raise ValueError("SECRET_KEY must be set in .env file")
    
    app.config['JSON_AS_ASCII'] = False
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
    app.config['TIMEZONE_STR'] = os.getenv('TIMEZONE')
    app.config['OLLAMA_URL'] = os.getenv('OLLAMA_URL')
    app.config['REDIS_URL'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    
    # LLM Chat model settings
    app.config['LLM_CHAT_MODEL'] = os.getenv('LLM_CHAT_MODEL')
    app.config['LLM_CHAT_MODEL_CONTEXT_WINDOW'] = int(os.getenv('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768))
    app.config['LLM_CHAT_TEMPERATURE'] = float(os.getenv('LLM_CHAT_TEMPERATURE', 0.1))
    app.config['LLM_CHAT_TOP_P'] = float(os.getenv('LLM_CHAT_TOP_P', 0.1))
    
    # LLM Multimodal model settings
    app.config['LLM_MULTIMODAL_MODEL'] = os.getenv('LLM_MULTIMODAL_MODEL')
    app.config['LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW'] = int(os.getenv('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768))
    app.config['LLM_MULTIMODAL_TEMPERATURE'] = float(os.getenv('LLM_MULTIMODAL_TEMPERATURE', 0.7))
    app.config['LLM_MULTIMODAL_TOP_P'] = float(os.getenv('LLM_MULTIMODAL_TOP_P', 0.9))
    
    # LLM Reasoning model settings
    app.config['LLM_REASONING_MODEL'] = os.getenv('LLM_REASONING_MODEL')
    app.config['LLM_REASONING_MODEL_CONTEXT_WINDOW'] = int(os.getenv('LLM_REASONING_MODEL_CONTEXT_WINDOW', 40960))
    app.config['LLM_REASONING_TEMPERATURE'] = float(os.getenv('LLM_REASONING_TEMPERATURE', 0.7))
    app.config['LLM_REASONING_TOP_P'] = float(os.getenv('LLM_REASONING_TOP_P', 0.9))
    
    # Automatic1111 settings
    app.config['AUTOMATIC1111_URL'] = os.getenv('AUTOMATIC1111_URL')
    app.config['AUTOMATIC1111_MODEL'] = os.getenv('AUTOMATIC1111_MODEL')
    app.config['MAX_IMAGE_WIDTH'] = int(os.getenv('MAX_IMAGE_WIDTH', 3840))
    app.config['MAX_IMAGE_HEIGHT'] = int(os.getenv('MAX_IMAGE_HEIGHT', 2160))
    app.config['MAX_IMAGE_SIZE_MB'] = int(os.getenv('MAX_IMAGE_SIZE_MB', 5))
    
    # Document upload settings
    app.config['MAX_DOCUMENT_SIZE_MB'] = int(os.getenv('MAX_DOCUMENT_SIZE_MB', 5))
    app.config['MAX_VOICE_SIZE_MB'] = int(os.getenv('MAX_VOICE_SIZE_MB', 5))
    app.config['MAX_AUDIO_SIZE_MB'] = int(os.getenv('MAX_AUDIO_SIZE_MB', 4))
    
    # Whisper ASR settings
    app.config['WHISPER_API_URL'] = os.getenv('WHISPER_API_URL', 'http://host.docker.internal:9000/asr')
    
    # Timeouts
    app.config['LLM_CHAT_TIMEOUT'] = int(os.getenv('LLM_CHAT_TIMEOUT', 60))
    app.config['LLM_MULTIMODAL_TIMEOUT'] = int(os.getenv('LLM_MULTIMODAL_TIMEOUT', 120))
    app.config['LLM_REASONING_TIMEOUT'] = int(os.getenv('LLM_REASONING_TIMEOUT', 300))
    app.config['AUTOMATIC1111_TIMEOUT'] = int(os.getenv('AUTOMATIC1111_TIMEOUT', 180))
    app.config['WHISPER_API_TIMEOUT'] = int(os.getenv('WHISPER_API_TIMEOUT', 120))
    
    # Camera settings
    app.config['CAMERA_API_URL'] = os.getenv('CAMERA_API_URL', 'http://host.docker.internal:5005')
    app.config['CAMERA_ENABLED'] = os.getenv('CAMERA_ENABLED', 'true').lower() in ('true', '1', 'yes')
    app.config['CAMERA_API_TIMEOUT'] = int(os.getenv('CAMERA_API_TIMEOUT', 15))
    app.config['CAMERA_CHECK_INTERVAL'] = int(os.getenv('CAMERA_CHECK_INTERVAL', 30))
    
    # Piper TTS settings
    app.config['PIPER_URL'] = os.getenv('PIPER_URL')
    app.config['PIPER_TIMEOUT'] = int(os.getenv('PIPER_TIMEOUT', 30))
    
    # Token estimation settings
    app.config['TOKEN_CHARS'] = int(os.getenv('TOKEN_CHARS', 3))
    app.config['CONTEXT_HISTORY_PERCENT'] = int(os.getenv('CONTEXT_HISTORY_PERCENT', 75))
    
    # Qdrant settings for RAG
    app.config['QDRANT_URL'] = os.getenv('QDRANT_URL')
    app.config['QDRANT_API_KEY'] = os.getenv('QDRANT_API_KEY')
    app.config['EMBEDDING_MODEL'] = os.getenv('EMBEDDING_MODEL', 'bge-m3:latest')
    app.config['RAG_CHUNK_SIZE'] = int(os.getenv('RAG_CHUNK_SIZE', 500))
    app.config['RAG_CHUNK_OVERLAP'] = int(os.getenv('RAG_CHUNK_OVERLAP', 50))
    app.config['RAG_TOP_K'] = int(os.getenv('RAG_TOP_K', 5))
    
    # Debug translations
    app.config['DEBUG_TRANSLATIONS'] = os.getenv('DEBUG_TRANSLATIONS', 'false').lower() == 'true'
    
    # Upload folder for images and files
    app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', 'data/uploads')
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Documents folder for uploaded documents
    app.config['DOCUMENTS_FOLDER'] = os.getenv('DOCUMENTS_FOLDER', 'data/documents')
    os.makedirs(app.config['DOCUMENTS_FOLDER'], exist_ok=True)
    
    # Timezone setup
    if app.config['TIMEZONE_STR']:
        try:
            app.config['TIMEZONE'] = pytz.timezone(app.config['TIMEZONE_STR'])
            app.logger.info(f"Using timezone: {app.config['TIMEZONE_STR']}")
        except UnknownTimeZoneError:
            app.logger.error(f"Unknown timezone '{app.config['TIMEZONE_STR']}'")
            app.config['TIMEZONE'] = None
    else:
        app.config['TIMEZONE'] = None
        app.logger.error("TIMEZONE not found in .env file")