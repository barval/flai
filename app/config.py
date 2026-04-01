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
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
    app.config['TIMEZONE_STR'] = os.getenv('TIMEZONE')
    app.config['REDIS_URL'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    
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
    
    # Timeouts for services (not model-specific, used for HTTP requests)
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
    
    # NEW: Context safety margin (use only 85% of calculated capacity)
    app.config['CONTEXT_SAFETY_MARGIN'] = float(os.getenv('CONTEXT_SAFETY_MARGIN', 0.85))
    
    # NEW: Maximum messages to load from history
    app.config['MAX_HISTORY_MESSAGES'] = int(os.getenv('MAX_HISTORY_MESSAGES', 30))
    
    # NEW: Enable token estimation debugging
    app.config['DEBUG_TOKEN_ESTIMATION'] = os.getenv('DEBUG_TOKEN_ESTIMATION', 'false').lower() == 'true'
    
    # Qdrant settings for RAG
    app.config['QDRANT_URL'] = os.getenv('QDRANT_URL')
    app.config['QDRANT_API_KEY'] = os.getenv('QDRANT_API_KEY')
    app.config['RAG_CHUNK_SIZE'] = int(os.getenv('RAG_CHUNK_SIZE', 500))
    app.config['RAG_CHUNK_OVERLAP'] = int(os.getenv('RAG_CHUNK_OVERLAP', 50))
    app.config['RAG_TOP_K'] = int(os.getenv('RAG_TOP_K', 15))
    
    # RAG relevance thresholds
    app.config['RAG_RELEVANCE_THRESHOLD_DEFAULT'] = float(os.getenv('RAG_RELEVANCE_THRESHOLD_DEFAULT', 0.3))
    app.config['RAG_RELEVANCE_THRESHOLD_REASONING'] = float(os.getenv('RAG_RELEVANCE_THRESHOLD_REASONING', 0.3))
    
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
    app.config['MAX_HISTORY_MESSAGES'] = int(os.getenv('MAX_HISTORY_MESSAGES', 30))
    app.config['CONTEXT_SAFETY_MARGIN'] = float(os.getenv('CONTEXT_SAFETY_MARGIN', 0.85))
    app.config['TEMPLATE_OVERHEAD_TOKENS'] = int(os.getenv('TEMPLATE_OVERHEAD_TOKENS', 800))

    # Image token estimation
    app.config['IMAGE_TOKENS_PER_IMAGE'] = int(os.getenv('IMAGE_TOKENS_PER_IMAGE', 1000))

    # File validation settings
    app.config['MAX_EXTENSION_LENGTH'] = int(os.getenv('MAX_EXTENSION_LENGTH', 10))