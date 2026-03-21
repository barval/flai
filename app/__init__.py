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
    migrate_add_embedding_model
)
from .queue import RedisRequestQueue
from .userdb import init_user_db, get_user_by_login
from modules import BaseModule, MultimodalModule, ImageModule, CamModule, RagModule, AudioModule
from modules.tts import TTSModule
import mimetypes

babel = Babel()

@babel.localeselector
def get_locale():
    if 'language' in session:
        return session['language']
    return request.accept_languages.best_match(['ru', 'en']) or 'ru'

def create_app():
    app = Flask(__name__)
    load_config(app)

    translations_path = os.path.join(app.root_path, '..', 'translations')
    app.config['BABEL_TRANSLATION_DIRECTORIES'] = translations_path
    app.config['BABEL_DEFAULT_LOCALE'] = 'ru'

    # Setup logging
    formatter = Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                          datefmt='%Y-%m-%d %H:%M:%S')
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    app.logger.handlers = [console_handler]
    app.logger.setLevel(logging.DEBUG)

    babel.init_app(app)
    app.jinja_env.add_extension('jinja2.ext.i18n')
    app.jinja_env.globals['_'] = gettext

    # Initialize databases
    init_db()
    migrate_db_add_response_fields(app)
    migrate_db_add_session_visits(app)
    migrate_db_add_indexes(app)
    migrate_db_add_index_status(app)
    migrate_add_model_configs(app)
    migrate_add_embedding_model(app)
    init_user_db()

    # Initialize modules
    modules = {}
    modules['base'] = BaseModule(app)
    if app.config.get('OLLAMA_URL'):
        modules['multimodal'] = MultimodalModule(app)
    if app.config.get('AUTOMATIC1111_URL') and 'multimodal' in modules:
        modules['image'] = ImageModule(app)
        modules['image'].set_multimodal_module(modules['multimodal'])
    if app.config.get('CAMERA_ENABLED'):
        modules['cam'] = CamModule(app)
    if app.config.get('QDRANT_URL'):
        modules['rag'] = RagModule(app)
    modules['audio'] = AudioModule(app)
    if app.config.get('PIPER_URL'):
        modules['tts'] = TTSModule(app)
    app.modules = modules

    app.request_queue = RedisRequestQueue(app)

    # Register blueprints (new modular structure)
    from .routes import auth, chat, admin, queue, tts
    app.register_blueprint(auth.bp)
    app.register_blueprint(chat.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(queue.bp)
    app.register_blueprint(tts.bp)

    # CLI
    from . import cli
    app.cli.add_command(cli.set_admin_password)

    # Additional camera routes
    if 'cam' in modules:
        from modules.cam import CamAPI
        CamAPI.register_routes(app, modules['cam'])

    # Ensure folders
    if not os.path.isabs(app.config['UPLOAD_FOLDER']):
        app.config['UPLOAD_FOLDER'] = os.path.abspath(app.config['UPLOAD_FOLDER'])
    if not os.path.isabs(app.config['DOCUMENTS_FOLDER']):
        app.config['DOCUMENTS_FOLDER'] = os.path.abspath(app.config['DOCUMENTS_FOLDER'])
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['DOCUMENTS_FOLDER'], exist_ok=True)

    # File serving endpoint
    @app.route('/api/files/<path:filename>')
    def serve_upload(filename):
        if 'login' not in session:
            abort(401)
        upload_folder = app.config['UPLOAD_FOLDER']
        safe_path = os.path.normpath(os.path.join(upload_folder, filename))
        if not safe_path.startswith(os.path.abspath(upload_folder)):
            app.logger.warning(f"Path traversal attempt: {filename}")
            abort(403)
        parts = filename.split('/')
        if len(parts) != 2:
            abort(400)
        session_id = parts[0]
        from .db import get_db
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT user_id FROM chat_sessions WHERE id = ?', (session_id,))
            row = c.fetchone()
            if not row or row[0] != session['login']:
                abort(403)
        try:
            if not os.path.exists(safe_path):
                abort(404)
            mimetype, _ = mimetypes.guess_type(safe_path)
            if not mimetype:
                ext = os.path.splitext(safe_path)[1].lower()
                if ext in ['.webm', '.wav', '.mp3', '.ogg', '.m4a', '.aac']:
                    mimetype = 'audio/webm'
                elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                    mimetype = 'image/jpeg'
                else:
                    mimetype = 'application/octet-stream'
            return send_file(safe_path, mimetype=mimetype, as_attachment=False)
        except Exception as e:
            app.logger.error(f"Error serving file {safe_path}: {e}")
            abort(404)

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