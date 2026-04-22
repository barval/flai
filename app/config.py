# app/config.py
# Configuration loader for FLAI application
import os
from datetime import timedelta
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
    app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH_MB', '50')) * 1024 * 1024
    app.config['TIMEZONE_STR'] = os.getenv('TIMEZONE')
    app.config['REDIS_URL'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    app.config['DEBUG_API_ENABLED'] = os.getenv('DEBUG_API_ENABLED', 'false').lower() == 'true'

    # Database configuration (PostgreSQL required)
    app.config['DATABASE_URL'] = os.getenv('DATABASE_URL')
    
    # llama.cpp server settings
    app.config['LLAMACPP_URL'] = os.getenv('LLAMACPP_URL')
    # stable-diffusion.cpp settings (uses sd-wrapper HTTP API)
    app.config['SD_MODEL_TYPE'] = os.getenv('SD_MODEL_TYPE', 'z_image_turbo')
    app.config['SD_EDIT_MODEL_TYPE'] = os.getenv('SD_EDIT_MODEL_TYPE', 'flux-2-klein-4b')
    app.config['SD_WRAPPER_URL'] = os.getenv('SD_WRAPPER_URL', 'http://flai-sd:7861')
    app.config['SD_CPP_TIMEOUT'] = int(os.getenv('SD_CPP_TIMEOUT', 900))  # 15 min for editing

    # Storage quotas (per user)
    app.config['MAX_UPLOAD_STORAGE_MB'] = int(os.getenv('MAX_UPLOAD_STORAGE_MB', 500))
    app.config['MAX_DOCUMENTS_STORAGE_MB'] = int(os.getenv('MAX_DOCUMENTS_STORAGE_MB', 50))
    app.config['MAX_DOCUMENTS_PER_USER'] = int(os.getenv('MAX_DOCUMENTS_PER_USER', 50))

    # Image validation settings (shared)
    app.config['MAX_IMAGE_WIDTH'] = int(os.getenv('MAX_IMAGE_WIDTH', 3840))
    app.config['MAX_IMAGE_HEIGHT'] = int(os.getenv('MAX_IMAGE_HEIGHT', 2160))
    app.config['MAX_IMAGE_SIZE_MB'] = int(os.getenv('MAX_IMAGE_SIZE_MB', 5))
    
    # Document upload settings
    app.config['MAX_DOCUMENT_SIZE_MB'] = int(os.getenv('MAX_DOCUMENT_SIZE_MB', 5))
    app.config['MAX_VOICE_SIZE_MB'] = int(os.getenv('MAX_VOICE_SIZE_MB', 5))
    app.config['MAX_AUDIO_SIZE_MB'] = int(os.getenv('MAX_AUDIO_SIZE_MB', 4))
    
    # Whisper ASR settings
    app.config['WHISPER_API_URL'] = os.getenv('WHISPER_API_URL', 'http://flai-whisper:9000/asr')
    
    # Timeouts for services (not model-specific, used for HTTP requests)
    app.config['WHISPER_API_TIMEOUT'] = int(os.getenv('WHISPER_API_TIMEOUT', 120))
    
    # Camera settings
    app.config['CAMERA_API_URL'] = os.getenv('CAMERA_API_URL', 'http://flai-room-snapshot-api:5000')
    app.config['CAMERA_ENABLED'] = os.getenv('CAMERA_ENABLED', 'true').lower() in ('true', '1', 'yes')
    app.config['CAMERA_API_TIMEOUT'] = int(os.getenv('CAMERA_API_TIMEOUT', 15))
    app.config['CAMERA_CHECK_INTERVAL'] = int(os.getenv('CAMERA_CHECK_INTERVAL', 30))
    
    # Piper TTS settings
    app.config['PIPER_URL'] = os.getenv('PIPER_URL')
    app.config['PIPER_TIMEOUT'] = int(os.getenv('PIPER_TIMEOUT', 30))
    
    # Token estimation settings
    app.config['TOKEN_CHARS'] = int(os.getenv('TOKEN_CHARS', 3))
    app.config['CONTEXT_HISTORY_PERCENT'] = int(os.getenv('CONTEXT_HISTORY_PERCENT', 75))
    
    # NEW: Context safety margin (use only 85% of calculated capacity)
    app.config['CONTEXT_SAFETY_MARGIN'] = float(os.getenv('CONTEXT_SAFETY_MARGIN', 0.85))
    
    # NEW: Maximum messages to load from history
    app.config['MAX_HISTORY_MESSAGES'] = int(os.getenv('MAX_HISTORY_MESSAGES', 30))
    
    # NEW: Enable token estimation debugging
    app.config['DEBUG_TOKEN_ESTIMATION'] = os.getenv('DEBUG_TOKEN_ESTIMATION', 'false').lower() == 'true'
    
    # Qdrant settings for RAG
    app.config['QDRANT_URL'] = os.getenv('QDRANT_URL')
    app.config['QDRANT_API_KEY'] = os.getenv('QDRANT_API_KEY')
    # RAG settings - defaults only, actual values come from DB
    app.config['RAG_CHUNK_SIZE'] = int(os.getenv('RAG_CHUNK_SIZE', 500))
    app.config['RAG_CHUNK_OVERLAP'] = int(os.getenv('RAG_CHUNK_OVERLAP', 50))
    app.config['RAG_CHUNK_STRATEGY'] = os.getenv('RAG_CHUNK_STRATEGY', 'fixed')
    app.config['RAG_TOP_K'] = int(os.getenv('RAG_TOP_K', 80))
    app.config['RAG_CONTEXT_PERCENT'] = int(os.getenv('RAG_CONTEXT_PERCENT', 30))

    # RAG relevance thresholds (used only if DB doesn't have values)
    app.config['RAG_RELEVANCE_THRESHOLD_DEFAULT'] = 0.3
    app.config['RAG_RELEVANCE_THRESHOLD_REASONING'] = 0.2

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

    # Session cookie settings for CSRF and security
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # Secure flag: True for HTTPS (production), False for HTTP (local development)
    # When behind HTTPS proxy (nginx), set HTTPS_ENABLED=true in .env
    app.config['SESSION_COOKIE_SECURE'] = os.getenv('HTTPS_ENABLED', 'false').lower() in ('true', '1', 'yes')
    
    # Session expiry - sessions expire after 8 hours of inactivity
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
    app.config['SESSION_REFRESH_EACH_OTHER'] = timedelta(hours=1)

    # CSRF configuration
    app.config['WTF_CSRF_ENABLED'] = True
    app.config['WTF_CSRF_CHECK_DEFAULT'] = True
    app.config['WTF_CSRF_IGNORE_LOCALHOST'] = False
    # CSRF token lifetime (1 hour)
    app.config['WTF_CSRF_TIME_LIMIT'] = 3600

    # Service retry settings
    app.config['SERVICE_RETRY_ATTEMPTS'] = int(os.getenv('SERVICE_RETRY_ATTEMPTS', 5))
    app.config['SERVICE_RETRY_DELAY'] = int(os.getenv('SERVICE_RETRY_DELAY', 2))

    # Redis queue settings
    app.config['REDIS_RESULT_TTL'] = int(os.getenv('REDIS_RESULT_TTL', 3600))
    app.config['QUEUE_MAX_WAIT_TIME'] = int(os.getenv('QUEUE_MAX_WAIT_TIME', 300))

    # Message pagination settings
    app.config['MESSAGES_DEFAULT_LIMIT'] = int(os.getenv('MESSAGES_DEFAULT_LIMIT', 100))
    app.config['MESSAGES_MAX_LIMIT'] = int(os.getenv('MESSAGES_MAX_LIMIT', 200))

    # Session and context settings
    app.config['TEMPLATE_OVERHEAD_TOKENS'] = int(os.getenv('TEMPLATE_OVERHEAD_TOKENS', 800))

    # Image token estimation
    app.config['IMAGE_TOKENS_PER_IMAGE'] = int(os.getenv('IMAGE_TOKENS_PER_IMAGE', 1000))

    # File validation settings
    app.config['MAX_EXTENSION_LENGTH'] = int(os.getenv('MAX_EXTENSION_LENGTH', 10))