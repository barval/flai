from flask import Flask
import logging
from logging import Formatter
import os

from .config import load_config
from .db import init_db, migrate_db_add_response_fields, migrate_db_add_session_visits
from .queue import RedisRequestQueue
from .userdb import init_user_db  # новая функция
from modules import BaseModule, MultimodalModule, ImageModule, CamModule, RagModule, AudioModule

def create_app():
    app = Flask(__name__)

    # Загрузка конфигурации
    load_config(app)

    # Настройка логирования
    formatter = Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                         datefmt='%Y-%m-%d %H:%M:%S')
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    app.logger.handlers = [console_handler]
    app.logger.setLevel(logging.DEBUG)

    # Инициализация БД чатов (теперь chats.db)
    init_db()
    migrate_db_add_response_fields()
    migrate_db_add_session_visits()

    # Инициализация БД пользователей
    init_user_db()

    # Инициализация модулей
    modules = {}
    modules['base'] = BaseModule(app)

    if app.config['LLM_MULTIMODAL_MODEL']:
        modules['multimodal'] = MultimodalModule(app)

    if app.config['AUTOMATIC1111_URL'] and 'multimodal' in modules:
        modules['image'] = ImageModule(app)
        modules['image'].set_multimodal_module(modules['multimodal'])

    modules['cam'] = CamModule(app)
    modules['rag'] = RagModule(app)
    modules['audio'] = AudioModule(app)

    app.modules = modules

    # Инициализация очереди Redis
    app.request_queue = RedisRequestQueue(app)

    # Контекстный процессор для подписи футера
    @app.context_processor
    def inject_footer():
        return dict(footer_content=app.config.get('FOOTER_TEXT', ""))

    # Регистрация маршрутов
    from . import auth, routes_chat, routes_queue, routes_admin, cli
    app.register_blueprint(auth.bp)
    app.register_blueprint(routes_chat.bp)
    app.register_blueprint(routes_queue.bp)
    app.register_blueprint(routes_admin.bp)  # новый blueprint админки

    # Регистрация CLI команд
    app.cli.add_command(cli.set_admin_password)

    # Дополнительная регистрация API для камер
    if 'cam' in modules:
        from modules.cam import CamAPI
        CamAPI.register_routes(app, modules['cam'])

    return app