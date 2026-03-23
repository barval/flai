# app/__init__.py
import os
from flask import Flask, request, session, send_file, abort, jsonify
from flask_babel import Babel, gettext
import logging
from logging import Formatter
from .config import load_config
from .db import (
    init_db, migrate_db_add_response_fields, migrate_db_add_session_visits,
    migrate_db_add_indexes, migrate_db_add_index_status, migrate_add_model_configs,
    migrate_add_embedding_model, migrate_add_ollama_url
)
from .queue import RedisRequestQueue
from .userdb import init_user_db, get_user_by_login
from modules import BaseModule, MultimodalModule, ImageModule, CamModule, RagModule, AudioModule
from modules.tts import TTSModule
import mimetypes

babel = Babel()


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

    # Explicit Babel configuration with absolute path
    translations_path = os.path.join(app.root_path, '..', 'translations')
    app.config['BABEL_TRANSLATION_DIRECTORIES'] = translations_path
    app.config['BABEL_DEFAULT_LOCALE'] = 'ru'

    # Setup logging
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    formatter = Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S')
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Configure the root logger so that all modules inherit the level
    logging.root.setLevel(log_level)
    logging.root.handlers = [console_handler]

    #app.logger.handlers = [console_handler]
    #app.logger.setLevel(log_level)
    app.logger.info(f"Logging initialized with level: {log_level_str}")

    # Initialize Babel with the app
    babel.init_app(app)
    app.jinja_env.add_extension('jinja2.ext.i18n')  # for _() in templates
    app.jinja_env.globals['_'] = gettext

    # Initialize chat DB
    init_db()
    migrate_db_add_response_fields(app)
    migrate_db_add_session_visits(app)
    migrate_db_add_indexes(app)  # Add indexes for performance
    migrate_db_add_index_status(app)  # Add index_status column to documents table for RAG
    migrate_add_model_configs(app)   # New migration for model configs
    migrate_add_embedding_model(app) # Add embedding_model column to documents table
    migrate_add_ollama_url(app)      # Add ollama_url column to model_configs table

    # Initialize user DB
    init_user_db()

    # Initialize modules
    modules = {}
    modules['base'] = BaseModule(app)

    # Multimodal module is always created if Ollama is available (model selected via admin)
    # No global OLLAMA_URL check needed – we rely on model configs.
    modules['multimodal'] = MultimodalModule(app)

    if app.config.get('AUTOMATIC1111_URL') and 'multimodal' in modules:
        modules['image'] = ImageModule(app)
        modules['image'].set_multimodal_module(modules['multimodal'])

    if app.config.get('CAMERA_ENABLED'):
        modules['cam'] = CamModule(app)
        app.logger.info("Camera module enabled")
    else:
        app.logger.info("Camera module disabled (CAMERA_ENABLED=False)")

    # Initialize RAG module if Qdrant URL is configured
    if app.config.get('QDRANT_URL'):
        modules['rag'] = RagModule(app)
        app.logger.info("RAG module enabled with Qdrant")
    else:
        app.logger.info("RAG module disabled (QDRANT_URL not set)")

    modules['audio'] = AudioModule(app)

    # TTS module
    if app.config.get('PIPER_URL'):
        modules['tts'] = TTSModule(app)
        app.logger.info("TTS module enabled")
    else:
        app.logger.info("TTS module disabled (PIPER_URL not set)")

    app.modules = modules

    # Initialize Redis queue
    app.request_queue = RedisRequestQueue(app)

    # Register blueprints (new modular structure)
    from .routes import auth, chat, admin, queue, tts, messages, sessions, documents
    app.register_blueprint(auth.bp)
    app.register_blueprint(chat.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(queue.bp)
    app.register_blueprint(tts.bp)
    app.register_blueprint(messages.bp)
    app.register_blueprint(sessions.bp)
    app.register_blueprint(documents.bp)

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
        # Security: ensure filename is within upload folder and doesn't contain path traversal
        upload_folder = app.config['UPLOAD_FOLDER']
        safe_path = os.path.normpath(os.path.join(upload_folder, filename))
        if not safe_path.startswith(os.path.abspath(upload_folder)):
            app.logger.warning(f"Path traversal attempt: {filename}")
            abort(403)
        # Check if file belongs to a session accessible by the user
        # filename format: session_id/unique_filename
        parts = filename.split('/')
        if len(parts) != 2:
            app.logger.warning(f"Invalid filename format: {filename}")
            abort(400)
        session_id = parts[0]
        # Verify that the session belongs to the current user
        from .db import get_db
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT user_id FROM chat_sessions WHERE id = ?', (session_id,))
            row = c.fetchone()
            if not row or row[0] != session['login']:
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

    return app