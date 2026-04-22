# app/__init__.py
import os
from flask import Flask, request, session, send_file, abort, jsonify, redirect, url_for
from flask_babel import Babel, gettext
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import logging
from logging import Formatter
from .config import load_config
from .database import init_db
from .resource_manager import get_resource_manager


from .queue import RedisRequestQueue
from .userdb import init_user_db, get_user_by_login
import mimetypes

babel = Babel()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)


def register_babel(app):
    """Register Babel locale selector after app is initialized."""
    @babel.localeselector
    def get_locale():
        """Select language from session or Accept-Language header."""
        if 'language' in session:
            return session['language']
        return request.accept_languages.best_match(['ru', 'en']) or 'ru'


def create_app():
    app = Flask(__name__)
    # Load configuration
    load_config(app)

    # Trust proxies for proper HTTPS detection behind nginx
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1)

    # Security headers for all responses
    @app.after_request
    def set_security_headers(response):
        """Add security headers to all responses."""
        # Content Security Policy - restrict resource loading
        # Allow media from self, blob:, and data: (for audio/video recordings)
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; media-src 'self' blob: data:; frame-ancestors 'none';"
        # Prevent MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Prevent clickjacking
        response.headers['X-Frame-Options'] = 'DENY'
        # XSS protection (legacy, but still useful for older browsers)
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # Referrer policy
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Permissions policy (formerly Feature-Policy)
        # Allow microphone and camera for voice messages and TTS
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(self), camera=(self)'
        return response

    # Explicit Babel configuration with absolute path
    translations_path = os.path.join(app.root_path, '..', 'translations')
    app.config['BABEL_TRANSLATION_DIRECTORIES'] = translations_path
    app.config['BABEL_DEFAULT_LOCALE'] = 'ru'

    # Setup logging
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    log_format = os.getenv('LOG_FORMAT', 'text').lower()

    if log_format == 'json':
        # JSON structured logging for ELK, Splunk, etc.
        try:
            from pythonjsonlogger import jsonlogger
            json_formatter = jsonlogger.JsonFormatter(
                fmt='%(asctime)s %(name)s %(levelname)s %(message)s %(pathname)s %(lineno)d',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(json_formatter)
            app.logger.info(f"Logging initialized with JSON format, level: {log_level_str}")
        except ImportError:
            # Fallback to text if python-json-logger not installed
            formatter = Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S')
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            app.logger.info(f"Logging initialized with TEXT format (json not available), level: {log_level_str}")
    else:
        # Standard text logging
        formatter = Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S')
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        app.logger.info(f"Logging initialized with TEXT format, level: {log_level_str}")

    # Configure the root logger so that all modules inherit the level
    logging.root.setLevel(log_level)
    logging.root.handlers = [console_handler]

    # Initialize Babel with the app
    register_babel(app)
    babel.init_app(app)
    app.jinja_env.add_extension('jinja2.ext.i18n')  # for _() in templates
    app.jinja_env.globals['_'] = gettext

    # Initialize CSRF protection
    csrf.init_app(app)
    app.config['CSRF_COOKIE_SAMESITE'] = 'Lax'
    app.config['WTF_CSRF_FORM_URL'] = False
    app.config['WTF_I18N_ENABLED'] = False

    # Initialize rate limiting
    limiter.init_app(app)

    # Initialize chat DB
    init_db()
    
    # Load RAG thresholds from DB if available
    from app.model_config import get_model_config
    chunks_cfg = get_model_config('chunks')
    if chunks_cfg:
        threshold_default = chunks_cfg.get('rag_threshold_default', 0.3)
        threshold_reasoning = chunks_cfg.get('rag_threshold_reasoning', 0.2)
        app.config['RAG_RELEVANCE_THRESHOLD_DEFAULT'] = threshold_default
        app.config['RAG_RELEVANCE_THRESHOLD_REASONING'] = threshold_reasoning
        app.logger.info(f"Loaded RAG thresholds from DB: default={threshold_default}, reasoning={threshold_reasoning}")

    # Detect hardware and initialize Resource Manager
    rm = get_resource_manager()
    app.logger.info(f"Hardware: {rm.get_status()}")

    # Pre-load GGUF models metadata for fast admin panel access
    try:
        from app.utils import get_gguf_models_cached
        gguf_models = get_gguf_models_cached('/models')
        app.logger.info(f"Preloaded GGUF metadata for {len(gguf_models)} models")
    except Exception as e:
        app.logger.warning(f"Could not preload GGUF metadata: {e}")

    # Initialize user DB
    init_user_db()

    # Initialize modules (lazy imports to avoid circular dependency)
    modules = {}

    from modules.base import BaseModule
    modules['base'] = BaseModule(app)

    from modules.multimodal import MultimodalModule
    modules['multimodal'] = MultimodalModule(app)

    if app.config.get('SD_WRAPPER_URL'):
        from modules.sd_cpp import SdCppModule
        modules['image'] = SdCppModule(app)
        modules['image'].set_multimodal_module(modules['multimodal'])
        app.logger.info("Image generation module enabled (sd-wrapper)")
    else:
        app.logger.info("Image generation module disabled (SD_WRAPPER_URL not set)")

    if app.config.get('CAMERA_ENABLED'):
        from modules.cam import CamModule
        modules['cam'] = CamModule(app)
        app.logger.info("Camera module enabled")
    else:
        app.logger.info("Camera module disabled (CAMERA_ENABLED=False)")

    if app.config.get('QDRANT_URL'):
        from modules.rag import RagModule
        modules['rag'] = RagModule(app)
        app.logger.info("RAG module enabled with Qdrant")
    else:
        app.logger.info("RAG module disabled (QDRANT_URL not set)")

    from modules.audio import AudioModule
    modules['audio'] = AudioModule(app)

    from modules.tts import TTSModule
    if app.config.get('PIPER_URL'):
        modules['tts'] = TTSModule(app)
        app.logger.info("TTS module enabled")
    else:
        app.logger.info("TTS module disabled (PIPER_URL not set)")

    app.modules = modules

    # Initialize Redis queue
    app.request_queue = RedisRequestQueue(app)

    # Register blueprints (new modular structure)
    from .routes import auth, chat, admin, queue, tts, messages, sessions, documents, backups
    app.register_blueprint(auth.bp)
    app.register_blueprint(chat.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(queue.bp)
    app.register_blueprint(tts.bp)
    app.register_blueprint(messages.bp)
    app.register_blueprint(sessions.bp)
    app.register_blueprint(documents.bp)
    app.register_blueprint(backups.bp)
    
    # Debug API endpoints (only when DEBUG_API_ENABLED=true)
    if app.config.get('DEBUG_API_ENABLED'):
        from .routes import debug
        # Exempt debug blueprint from CSRF
        csrf.exempt(debug.bp)
        app.register_blueprint(debug.bp)
        app.logger.info("Debug API enabled")

    # Register CLI commands
    from . import cli
    app.cli.add_command(cli.set_admin_password)

    # Additional camera routes
    if 'cam' in modules:
        from modules.cam import CamAPI
        CamAPI.register_routes(app, modules['cam'])

    # Ensure UPLOAD_FOLDER is an absolute path
    if not os.path.isabs(app.config['UPLOAD_FOLDER']):
        app.config['UPLOAD_FOLDER'] = os.path.abspath(app.config['UPLOAD_FOLDER'])
    app.logger.info(f"Upload folder: {app.config['UPLOAD_FOLDER']}")

    # Ensure DOCUMENTS_FOLDER is an absolute path
    if not os.path.isabs(app.config['DOCUMENTS_FOLDER']):
        app.config['DOCUMENTS_FOLDER'] = os.path.abspath(app.config['DOCUMENTS_FOLDER'])
    app.logger.info(f"Documents folder: {app.config['DOCUMENTS_FOLDER']}")

    # File serving endpoint
    @app.route('/api/files/<path:filename>')
    def serve_upload(filename):
        """Serve uploaded files after checking user permissions."""
        if 'login' not in session:
            abort(401)
        
        # Security: prevent path traversal attacks
        # Reject any filename containing path traversal sequences
        if '..' in filename or filename.startswith('/') or filename.startswith('\\'):
            app.logger.warning(f"Path traversal attempt blocked: {filename}")
            abort(403)
        
        # Security: ensure filename is within upload folder
        upload_folder = app.config['UPLOAD_FOLDER']
        safe_path = os.path.normpath(os.path.join(upload_folder, filename))
        if not safe_path.startswith(os.path.abspath(upload_folder) + os.sep):
            app.logger.warning(f"Path traversal attempt blocked: {filename}")
            abort(403)
        
        # Check if file belongs to a session accessible by the user
        # filename format: session_id/unique_filename
        parts = filename.split('/')
        if len(parts) != 2:
            app.logger.warning(f"Invalid filename format: {filename}")
            abort(400)
        session_id = parts[0]
        # Verify that the session belongs to the current user
        from .database import get_db
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT user_id FROM chat_sessions WHERE id = %s', (session_id,))
            row = c.fetchone()
            if not row or row['user_id'] != session['login']:
                app.logger.warning(f"User {session['login']} tried to access session {session_id}")
                abort(403)
        # Send file
        try:
            if not os.path.exists(safe_path):
                app.logger.error(f"File not found: {safe_path}")
                abort(404)
            # Detect mimetype from filename
            mimetype, _ = mimetypes.guess_type(safe_path)
            if not mimetype:
                # Fallback based on extension
                ext = os.path.splitext(safe_path)[1].lower()
                if ext in ['.webm', '.wav', '.mp3', '.ogg', '.m4a', '.aac']:
                    mimetype = 'audio/webm'
                elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                    mimetype = 'image/jpeg'
                else:
                    mimetype = 'application/octet-stream'
            app.logger.info(f"Serving file: {safe_path} (mimetype: {mimetype})")
            return send_file(safe_path, mimetype=mimetype, as_attachment=False)
        except Exception as e:
            app.logger.error(f"Error serving file {safe_path}: {e}")
            abort(404)

    # Global error handlers for API routes
    @app.errorhandler(400)
    def bad_request(error):
        """Handle 400 errors — CSRF failures return session_expired for API."""
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Session expired. Please refresh the page.', 'session_expired': True}), 400
        return error

    @app.errorhandler(401)
    def unauthorized(error):
        """Handle 401 errors — redirect to login for HTML, JSON for API."""
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Authentication required.', 'session_expired': True}), 401
        return redirect(url_for('auth.login')) if not request.is_json else error

    @app.errorhandler(403)
    def forbidden(error):
        """Handle 403 errors — session expired or forbidden access."""
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Session expired. Please refresh the page.', 'session_expired': True}), 403
        return error

    @app.errorhandler(500)
    def internal_error(error):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Internal server error'}), 500
        return error

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Not found'}), 404
        return error

    # Comprehensive health check endpoint
    @app.route('/health')
    def health_check():
        """Comprehensive health check for all services."""
        from datetime import datetime, timezone
        import requests
        from .database import get_db

        status = {
            'status': 'ok',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'services': {
                'web': 'ok',
                'database': 'unknown',
                'redis': 'unknown',
                'llamacpp': 'unknown',
            }
        }
        http_status = 200

        # Check database
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute('SELECT 1')
            status['services']['database'] = 'ok'
        except Exception as e:
            status['services']['database'] = 'error'
            app.logger.error(f"Health check - Database error: {e}")
            http_status = 503
        
        # Check Redis
        try:
            app.request_queue.redis.ping()
            status['services']['redis'] = 'ok'
        except Exception as e:
            status['services']['redis'] = 'error'
            app.logger.error(f"Health check - Redis error: {e}")
            http_status = 503
        
        # Check llama-server
        try:
            # Prefer LLAMACPP_URL from config (global setting)
            service_url = app.config.get('LLAMACPP_URL')
            if not service_url:
                # Fallback to DB config
                from app.model_config import get_model_config
                llamacpp_config = get_model_config('chat')
                if llamacpp_config:
                    service_url = llamacpp_config.get('service_url')
            if not service_url:
                service_url = 'http://flai-llamacpp:8033'
            response = requests.get(f"{service_url.rstrip('/')}/v1/models", timeout=5)
            if response.status_code == 200:
                status['services']['llamacpp'] = 'ok'
            else:
                status['services']['llamacpp'] = 'error'
                http_status = 503
        except Exception as e:
            status['services']['llamacpp'] = 'error'
            app.logger.error(f"Health check - llama-server error: {e}")
            http_status = 503

        # Check sd-wrapper (image generation/editing)
        sd_url = app.config.get('SD_WRAPPER_URL')
        if sd_url:
            try:
                response = requests.get(f"{sd_url.rstrip('/')}/health", timeout=5)
                status['services']['sd_wrapper'] = 'ok' if response.status_code == 200 else 'error'
            except Exception as e:
                status['services']['sd_wrapper'] = 'error'
                app.logger.error(f"Health check - sd-wrapper error: {e}")

        # Check Whisper ASR
        whisper_url = app.config.get('WHISPER_API_URL')
        if whisper_url:
            try:
                base_url = whisper_url.replace('/asr', '').rstrip('/')
                response = requests.get(f"{base_url}/docs", timeout=5)
                status['services']['whisper'] = 'ok' if response.status_code == 200 else 'error'
            except Exception:
                status['services']['whisper'] = 'error'

        # Check Qdrant (RAG)
        qdrant_url = app.config.get('QDRANT_URL')
        qdrant_api_key = app.config.get('QDRANT_API_KEY')
        if qdrant_url:
            try:
                headers = {}
                if qdrant_api_key:
                    headers['api-key'] = qdrant_api_key
                response = requests.get(f"{qdrant_url.rstrip('/')}/collections", headers=headers, timeout=5)
                status['services']['qdrant'] = 'ok' if response.status_code == 200 else 'error'
            except Exception:
                status['services']['qdrant'] = 'error'

        # Determine overall status
        services_ok = sum(1 for v in status['services'].values() if v == 'ok')
        services_total = len(status['services'])
        if services_ok == services_total:
            status['status'] = 'ok'
        elif services_ok > 0:
            status['status'] = 'degraded'
        else:
            status['status'] = 'error'
        
        return jsonify(status), http_status

    # Prometheus metrics endpoint
    @app.route('/metrics')
    def metrics():
        """Prometheus-compatible metrics endpoint."""
        import time
        
        # Collect metrics
        metrics_output = []
        
        # System metrics
        metrics_output.append('# HELP flai_web_info Web service information')
        metrics_output.append('# TYPE flai_web_info gauge')
        metrics_output.append(f'flai_web_info{{version="1.0.0"}} 1')
        
        # Queue metrics
        try:
            queue_length = app.request_queue.redis.llen(app.request_queue.queue_key)
            processing_count = app.request_queue.redis.hlen(app.request_queue.processing_key)
            
            metrics_output.append('')
            metrics_output.append('# HELP flai_queue_length Current queue length')
            metrics_output.append('# TYPE flai_queue_length gauge')
            metrics_output.append(f'flai_queue_length {queue_length}')
            
            metrics_output.append('')
            metrics_output.append('# HELP flai_queue_processing Number of tasks being processed')
            metrics_output.append('# TYPE flai_queue_processing gauge')
            metrics_output.append(f'flai_queue_processing {processing_count}')
        except Exception as e:
            app.logger.error(f"Metrics - Queue error: {e}")
        
        # Database metrics
        try:
            # PostgreSQL: cannot easily determine size from app container
            db_size = 0
            metrics_output.append('')
            metrics_output.append('# HELP flai_database_size_bytes Database size (0 for PostgreSQL)')
            metrics_output.append('# TYPE flai_database_size_bytes gauge')
            metrics_output.append(f'flai_database_size_bytes {db_size}')
        except Exception as e:
            app.logger.error(f"Metrics - Database error: {e}")
        
        # Request metrics (in-memory counter)
        if not hasattr(app, '_request_counter'):
            app._request_counter = 0
        app._request_counter += 1
        
        metrics_output.append('')
        metrics_output.append('# HELP flai_requests_total Total number of requests')
        metrics_output.append('# TYPE flai_requests_total counter')
        metrics_output.append(f'flai_requests_total {app._request_counter}')
        
        # Uptime metric
        if not hasattr(app, '_start_time'):
            app._start_time = time.time()
        uptime = time.time() - app._start_time
        
        metrics_output.append('')
        metrics_output.append('# HELP flai_uptime_seconds Service uptime in seconds')
        metrics_output.append('# TYPE flai_uptime_seconds counter')
        metrics_output.append(f'flai_uptime_seconds {uptime:.0f}')
        
        return '\n'.join(metrics_output) + '\n', 200, {'Content-Type': 'text/plain; charset=utf-8'}

    return app