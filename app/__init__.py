from flask import Flask, request, session
from flask_babel import Babel
import logging
from logging import Formatter
import os

from .config import load_config
from .db import init_db, migrate_db_add_response_fields, migrate_db_add_session_visits
from .queue import RedisRequestQueue
from .userdb import init_user_db
from modules import BaseModule, MultimodalModule, ImageModule, CamModule, RagModule, AudioModule

babel = Babel()

@babel.localeselector
def get_locale():
    # Language from session or Accept-Language header
    if 'language' in session:
        return session['language']
    return request.accept_languages.best_match(['ru', 'en']) or 'ru'

def create_app():
    app = Flask(__name__)

    # Load configuration
    load_config(app)

    # Setup logging
    formatter = Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                         datefmt='%Y-%m-%d %H:%M:%S')
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    app.logger.handlers = [console_handler]
    app.logger.setLevel(logging.DEBUG)

    # Initialize Babel
    babel.init_app(app)
    app.jinja_env.add_extension('jinja2.ext.i18n')  # for _() in templates

    # Initialize chat DB
    init_db()
    migrate_db_add_response_fields()
    migrate_db_add_session_visits()

    # Initialize user DB
    init_user_db()

    # Initialize modules
    modules = {}
    modules['base'] = BaseModule(app)

    if app.config['LLM_MULTIMODAL_MODEL']:
        modules['multimodal'] = MultimodalModule(app)

    if app.config['AUTOMATIC1111_URL'] and 'multimodal' in modules:
        modules['image'] = ImageModule(app)
        modules['image'].set_multimodal_module(modules['multimodal'])

    if app.config['CAMERA_ENABLED']:
        modules['cam'] = CamModule(app)
        app.logger.info("Camera module enabled")
    else:
        app.logger.info("Camera module disabled (CAMERA_ENABLED=False)")

    modules['rag'] = RagModule(app)
    modules['audio'] = AudioModule(app)

    app.modules = modules

    # Initialize Redis queue
    app.request_queue = RedisRequestQueue(app)

    # Register blueprints
    from . import auth, routes_chat, routes_queue, routes_admin, cli
    app.register_blueprint(auth.bp)
    app.register_blueprint(routes_chat.bp)
    app.register_blueprint(routes_queue.bp)
    app.register_blueprint(routes_admin.bp)

    # Register CLI commands
    app.cli.add_command(cli.set_admin_password)

    # Additional camera routes
    if 'cam' in modules:
        from modules.cam import CamAPI
        CamAPI.register_routes(app, modules['cam'])

    return app