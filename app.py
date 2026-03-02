# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
import os
import sqlite3
import json
import base64
from datetime import datetime
from dotenv import load_dotenv
import mimetypes
import uuid
import requests
import time
import pytz
from pytz.exceptions import UnknownTimeZoneError
import logging
from logging import Formatter
import threading
import redis
import pickle
from collections import defaultdict

# Импорт модулей
from modules import BaseModule, MultimodalModule, ImageModule, CamModule, RagModule, AudioModule

load_dotenv()

app = Flask(__name__)

# Проверяем наличие SECRET_KEY в .env
secret_key = os.getenv('SECRET_KEY')
if not secret_key:
    app.logger.error("SECRET_KEY не найден в .env файле. Приложение не может быть запущено.")
    raise ValueError("SECRET_KEY must be set in .env file")
app.secret_key = secret_key

app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# Загружаем все переменные из .env в конфиг Flask
app.config.update({
    'FOOTER_TEXT': os.getenv('FOOTER_TEXT'),
    'TIMEZONE_STR': os.getenv('TIMEZONE'),
    'OLLAMA_URL': os.getenv('OLLAMA_URL'),
    'REDIS_URL': os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    'LLM_CHAT_MODEL': os.getenv('LLM_CHAT_MODEL'),
    'LLM_CHAT_MODEL_CONTEXT_WINDOW': int(os.getenv('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768)),
    'LLM_CHAT_TEMPERATURE': float(os.getenv('LLM_CHAT_TEMPERATURE', 0.1)),
    'LLM_CHAT_TOP_P': float(os.getenv('LLM_CHAT_TOP_P', 0.1)),
    'LLM_MULTIMODAL_MODEL': os.getenv('LLM_MULTIMODAL_MODEL'),
    'LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW': int(os.getenv('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768)),
    'LLM_MULTIMODAL_TEMPERATURE': float(os.getenv('LLM_MULTIMODAL_TEMPERATURE', 0.7)),
    'LLM_MULTIMODAL_TOP_P': float(os.getenv('LLM_MULTIMODAL_TOP_P', 0.9)),
    'LLM_REASONING_MODEL': os.getenv('LLM_REASONING_MODEL'),
    'LLM_REASONING_MODEL_CONTEXT_WINDOW': int(os.getenv('LLM_REASONING_MODEL_CONTEXT_WINDOW', 40960)),
    'LLM_REASONING_TEMPERATURE': float(os.getenv('LLM_REASONING_TEMPERATURE', 0.7)),
    'LLM_REASONING_TOP_P': float(os.getenv('LLM_REASONING_TOP_P', 0.9)),
    'AUTOMATIC1111_URL': os.getenv('AUTOMATIC1111_URL'),
    'AUTOMATIC1111_MODEL': os.getenv('AUTOMATIC1111_MODEL'),
    'MAX_IMAGE_WIDTH': int(os.getenv('MAX_IMAGE_WIDTH', 3840)),
    'MAX_IMAGE_HEIGHT': int(os.getenv('MAX_IMAGE_HEIGHT', 2160)),
    'MAX_IMAGE_SIZE_MB': int(os.getenv('MAX_IMAGE_SIZE_MB', 5)),
    # URL для Whisper API
    'WHISPER_API_URL': os.getenv('WHISPER_API_URL', 'http://host.docker.internal:9000/asr')
})

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

# Настройка логирования
formatter = Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
                     datefmt='%Y-%m-%d %H:%M:%S')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
app.logger.handlers = [console_handler]
app.logger.setLevel(logging.DEBUG)

# Инициализация модулей
modules = {}

# Базовый модуль (всегда инициализируем)
modules['base'] = BaseModule(app)

# Мультимодальный модуль (если доступен)
if app.config['LLM_MULTIMODAL_MODEL']:
    modules['multimodal'] = MultimodalModule(app)

# Модуль генерации изображений (если доступен)
if app.config['AUTOMATIC1111_URL'] and 'multimodal' in modules:
    modules['image'] = ImageModule(app)
    # Связываем с мультимодальным модулем
    modules['image'].set_multimodal_module(modules['multimodal'])
else:
    app.logger.info("ImageModule не инициализирован (требуются Automatic1111_URL и мультимодальный модуль)")

# Модуль видеонаблюдения
modules['cam'] = CamModule(app)

# Регистрируем API эндпоинты для камер
if 'cam' in modules:
    from modules.cam import CamAPI
    CamAPI.register_routes(app, modules['cam'])
    app.logger.info("API эндпоинты для камер зарегистрированы")

# Модули
modules['rag'] = RagModule(app)
modules['audio'] = AudioModule(app)

# -------------------------------
# Пути к данным и шаблонам
# -------------------------------
DATA_DIR = 'data'
PROMPTS_DIR = 'prompts'

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

CHAT_DB_PATH = os.path.join(DATA_DIR, 'chat.db')

# -------------------------------
# Функция для загрузки шаблона промпта
# -------------------------------
def load_prompt_template(template_name):
    """Загружает шаблон промпта из файла"""
    template_path = os.path.join(PROMPTS_DIR, template_name)
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        app.logger.error(f"Шаблон не найден: {template_path}")
        return None
    except Exception as e:
        app.logger.error(f"Ошибка загрузки шаблона {template_name}: {str(e)}")
        return None

# -------------------------------
# Функция для форматирования промпта с переменными
# -------------------------------
def format_prompt(template_name, variables):
    """Загружает шаблон и подставляет переменные"""
    template = load_prompt_template(template_name)
    if not template:
        app.logger.error(f"Шаблон {template_name} не загружен")
        return None
    
    try:
        return template.format(**variables)
    except KeyError as e:
        app.logger.error(f"Отсутствует переменная в шаблоне {template_name}: {e}")
        return None
    except Exception as e:
        app.logger.error(f"Ошибка форматирования шаблона {template_name}: {str(e)}")
        return None

# -------------------------------
# Функция для получения текущего времени в заданном часовом поясе
# -------------------------------
def get_current_time_in_timezone():
    """Возвращает текущее время в часовом поясе, указанном в .env"""
    if not app.config.get('TIMEZONE'):
        app.logger.error("Часовой пояс не настроен в .env файле")
        return None
    
    try:
        utc_now = datetime.now(pytz.UTC)
        local_time = utc_now.astimezone(app.config['TIMEZONE'])
        
        weekdays_ru = {
            0: 'понедельник', 1: 'вторник', 2: 'среда',
            3: 'четверг', 4: 'пятница', 5: 'суббота', 6: 'воскресенье'
        }
        
        formatted_date = local_time.strftime('%d.%m.%Y')
        formatted_time = local_time.strftime('%H:%M:%S')
        weekday_ru = weekdays_ru[local_time.weekday()]
        
        tz_abbr = local_time.strftime('%z')
        if tz_abbr:
            tz_abbr = f"(+{int(tz_abbr[1:3])})" if tz_abbr.startswith('+') else f"({tz_abbr})"
        else:
            tz_abbr = ""
        
        return f"{formatted_date} {formatted_time} {weekday_ru} {tz_abbr}"
    
    except Exception as e:
        app.logger.error(f"Ошибка получения времени: {str(e)}")
        return None

def get_current_time_in_timezone_for_db():
    """Возвращает текущее время в формате SQLite"""
    if not app.config.get('TIMEZONE'):
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        utc_now = datetime.now(pytz.UTC)
        local_time = utc_now.astimezone(app.config['TIMEZONE'])
        return local_time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        app.logger.error(f"Ошибка получения времени: {str(e)}")
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# -------------------------------
# Функции для работы с БД
# -------------------------------
def init_db():
    """Инициализация базы данных"""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            
            c.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    user_id TEXT PRIMARY KEY,
                    last_session_id TEXT
                )
            ''')
            
            c.execute('''
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    title TEXT,
                    model_name TEXT DEFAULT "auto",
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            c.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    file_data TEXT,
                    file_type TEXT,
                    file_name TEXT,
                    model_name TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            app.logger.info(f"Database initialized successfully at {CHAT_DB_PATH}")
    except Exception as e:
        app.logger.error(f"Failed to initialize database: {str(e)}")
        raise

def migrate_db_add_response_fields():
    """Добавление полей для хранения раздельного времени ответа"""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            
            # Проверяем существование колонок и добавляем их, если нужно
            c.execute("PRAGMA table_info(messages)")
            columns = [col[1] for col in c.fetchall()]
            
            if 'response_time' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN response_time TEXT')
                app.logger.info("Добавлена колонка response_time")
            
            if 'mm_time' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN mm_time TEXT')
                app.logger.info("Добавлена колонка mm_time")
            
            if 'gen_time' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN gen_time TEXT')
                app.logger.info("Добавлена колонка gen_time")
            
            if 'mm_model' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN mm_model TEXT')
                app.logger.info("Добавлена колонка mm_model")
            
            if 'gen_model' not in columns:
                c.execute('ALTER TABLE messages ADD COLUMN gen_model TEXT')
                app.logger.info("Добавлена колонка gen_model")
            
            conn.commit()
            app.logger.info("Миграция БД для полей ответа выполнена")
    except Exception as e:
        app.logger.error(f"Ошибка миграции БД: {str(e)}")

def migrate_db_add_session_visits():
    """Добавление таблицы для отслеживания последних посещений сеансов"""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            c.execute('''
                CREATE TABLE IF NOT EXISTS session_visits (
                    user_id TEXT,
                    session_id TEXT,
                    last_visit DATETIME,
                    PRIMARY KEY (user_id, session_id)
                )
            ''')
            conn.commit()
            app.logger.info("Таблица session_visits создана или уже существует")
    except Exception as e:
        app.logger.error(f"Ошибка миграции session_visits: {str(e)}")

# Инициализация и миграция
init_db()
migrate_db_add_response_fields()
migrate_db_add_session_visits()

# -------------------------------
# Функции для работы с пользователями
# -------------------------------
def load_users():
    users = {}
    users_file = 'users.list'
    if os.path.exists(users_file):
        with open(users_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                    
                parts = line.split(',')
                if len(parts) >= 2:
                    email = parts[0].strip()
                    password = parts[1].strip()
                    service_class = int(parts[2].strip()) if len(parts) >= 3 else 2  # По умолчанию низший
                    
                    if email and password and service_class in [0, 1, 2]:
                        users[email] = {
                            'password': password,
                            'service_class': service_class
                        }
                    else:
                        app.logger.error(f"Некорректные данные в строке {line_num}")
                else:
                    app.logger.error(f"Некорректная строка {line_num}")
        
        if not users:
            app.logger.error("В файле users.list нет валидных записей")
    else:
        app.logger.error("users.list not found")
    
    return users

USERS = load_users()

# -------------------------------
# Класс RedisRequestQueue (менеджер очереди на Redis)
# -------------------------------
class RedisRequestQueue:
    def __init__(self, redis_url):
        self.redis = redis.from_url(redis_url, decode_responses=False)
        self.queue_key = 'request_queue'
        self.processing_key = 'processing_requests'
        self.results_key = 'request_results'
        self.user_requests_key = 'user_requests'
        
        # Запускаем обработчик в отдельном потоке
        self.start_worker()
    
    def start_worker(self):
        """Запускает воркер для обработки очереди в отдельном потоке"""
        thread = threading.Thread(target=self._worker_loop, daemon=True)
        thread.start()
        app.logger.info("RedisRequestQueue: воркер запущен")
    
    def _worker_loop(self):
        """Основной цикл обработки очереди"""
        app.logger.info("RedisRequestQueue: запуск цикла обработки")
        
        while True:
            try:
                # Блокирующее получение задачи из очереди (ждем 5 секунд)
                result = self.redis.blpop(self.queue_key, timeout=5)
                
                if not result:
                    # Нет задач, продолжаем ждать
                    continue
                
                # Получаем данные задачи
                queue_key, task_data = result
                task = pickle.loads(task_data)
                
                app.logger.info(f"RedisRequestQueue: получена задача {task['id']} из очереди для сеанса {task['session_id']}")
                
                # Помечаем задачу как обрабатываемую
                self.redis.hset(self.processing_key, task['id'], task_data)
                
                try:
                    # Обрабатываем запрос
                    result_data = self._process_request(task)
                    
                    # Убеждаемся, что в результате есть session_id
                    if 'session_id' not in result_data:
                        result_data['session_id'] = task['session_id']
                    
                    # Сохраняем результат
                    self.redis.hset(self.results_key, task['id'], pickle.dumps({
                        'status': 'completed',
                        'result': result_data,
                        'timestamp': time.time()
                    }))
                    
                    app.logger.info(f"RedisRequestQueue: задача {task['id']} выполнена успешно для сеанса {task['session_id']}")
                    
                except Exception as e:
                    app.logger.error(f"RedisRequestQueue: ошибка обработки задачи {task['id']}: {str(e)}")
                    self.redis.hset(self.results_key, task['id'], pickle.dumps({
                        'status': 'error',
                        'error': str(e),
                        'result': {'session_id': task['session_id']},
                        'timestamp': time.time()
                    }))
                
                finally:
                    # Удаляем из обрабатываемых
                    self.redis.hdel(self.processing_key, task['id'])
                    
            except Exception as e:
                app.logger.error(f"RedisRequestQueue: ошибка в worker loop: {str(e)}")
                time.sleep(1)
    
    def add_request(self, user_id, session_id, request_data, user_class):
        """
        Добавление запроса в очередь Redis
        Возвращает request_id и информацию о позиции
        """
        request_id = str(uuid.uuid4())
        timestamp = time.time()  # Время постановки в очередь
        
        task = {
            'id': request_id,
            'user_id': user_id,
            'session_id': session_id,
            'data': request_data,
            'timestamp': timestamp,  # Сохраняем время создания
            'user_class': user_class,
            'session_title': self._get_session_title(session_id)
        }
        
        app.logger.info(f"RedisRequestQueue.add_request: добавление задачи {request_id} для сеанса {session_id} с временем {timestamp}")
        
        # Сохраняем задачу в очередь Redis
        self.redis.rpush(self.queue_key, pickle.dumps(task))
        
        # Сохраняем связь пользователь -> запросы
        self.redis.sadd(f"{self.user_requests_key}:{user_id}", request_id)
        
        # Получаем позицию в очереди
        queue_length = self.redis.llen(self.queue_key)
        
        # Оцениваем время ожидания
        estimated_wait = max(1, queue_length * 5)
        
        position_info = {
            'position': queue_length,
            'estimated_seconds': estimated_wait
        }
        
        app.logger.info(f"RedisRequestQueue.add_request: задача добавлена, позиция={queue_length}")
        
        return request_id, position_info
    
    def get_user_queue_counts(self, user_id):
        """
        Возвращает (количество запросов пользователя в очереди, общее количество в очереди)
        """
        total = self.redis.llen(self.queue_key)
        if total == 0:
            return 0, 0
        # Получаем все элементы очереди (они хранятся в pickle)
        items = self.redis.lrange(self.queue_key, 0, -1)
        user_count = 0
        for item in items:
            try:
                task = pickle.loads(item)
                if task.get('user_id') == user_id:
                    user_count += 1
            except Exception as e:
                app.logger.error(f"Ошибка десериализации задачи: {e}")
                continue
        return user_count, total
    
    def _get_session_title(self, session_id):
        """Получить заголовок сеанса по ID"""
        try:
            with sqlite3.connect(CHAT_DB_PATH) as conn:
                c = conn.cursor()
                c.execute('SELECT title FROM chat_sessions WHERE id = ?', (session_id,))
                row = c.fetchone()
                return row[0] if row else "Неизвестный сеанс"
        except Exception as e:
            app.logger.error(f"Ошибка получения заголовка сессии: {str(e)}")
            return "Неизвестный сеанс"
    
    def _process_request(self, task):
        """
        Обработка запроса
        Здесь вызываются соответствующие модули
        """
        app.logger.info(f"RedisRequestQueue._process_request: обработка задачи {task['id']} для сеанса {task['session_id']}")
        
        user_id = task['user_id']
        session_id = task['session_id']
        request_data = task['data']
        
        # Фиксируем время НАЧАЛА обработки задачи (когда она достаётся из очереди)
        processing_start_time = time.time()
        
        # Получаем текущее время для логирования
        current_time_str = get_current_time_in_timezone()
        
        # Определяем тип запроса
        request_type = request_data.get('type', 'text')
        message_text = request_data.get('text', '')
        file_data = request_data.get('file_data')
        file_type = request_data.get('file_type')
        file_name = request_data.get('file_name')
        
        app.logger.info(f"RedisRequestQueue._process_request: тип={request_type}, текст='{message_text}'")
        
        # Для текстовых запросов
        if request_type == 'text':
            app.logger.info("RedisRequestQueue._process_request: обработка текстового запроса")
            
            # Фиксируем время НАЧАЛА работы маршрутизатора
            router_start_time = time.time()
            router_result = modules['base'].process_message(message_text, current_time_str)
            router_time = round(time.time() - router_start_time, 1)
            app.logger.info(f"RedisRequestQueue._process_request: router_result={router_result}, время маршрутизатора: {router_time} сек")
            
            if 'error' in router_result:
                # Время ЗАВЕРШЕНИЯ обработки
                completion_time_for_db = get_current_time_in_timezone_for_db()
                return {
                    'error': router_result['error'],
                    'session_id': session_id,
                    'assistant_timestamp': completion_time_for_db,
                    'is_error': True,
                    'response_time': router_time  # Время работы маршрутизатора
                }
            
            action_type = router_result['action']
            query = router_result['query']
            
            final_response = ""
            model_used = app.config['LLM_CHAT_MODEL']
            model_category = 'chat'
            is_error = False
            process_time = 0
            
            if action_type == 'image':
                model_category = 'image'
                if 'image' in modules and modules['image'].available:
                    # Замеряем время работы мультимодальной модели (генерация параметров)
                    mm_start_time = time.time()
                    
                    # Генерируем параметры через мультимодальную модель
                    prompt_data, error = modules['multimodal'].generate_image_params(query)
                    
                    mm_time = round(time.time() - mm_start_time, 1)
                    
                    if error:
                        final_response = f"⚠️ {error}"
                        model_used = 'system'
                        is_error = True
                        process_time = mm_time
                    else:
                        # Замеряем время генерации изображения в Automatic1111
                        gen_start_time = time.time()
                        
                        image_result = modules['image']._call_automatic1111(prompt_data)
                        
                        gen_time = round(time.time() - gen_start_time, 1)
                        
                        if image_result['success']:
                            # Время ЗАВЕРШЕНИЯ генерации
                            completion_time_for_db = get_current_time_in_timezone_for_db()
                            
                            # Сохраняем оба времени в результат
                            image_result['mm_time'] = mm_time
                            image_result['gen_time'] = gen_time
                            image_result['mm_model'] = app.config['LLM_MULTIMODAL_MODEL']
                            image_result['gen_model'] = app.config['AUTOMATIC1111_MODEL']
                            
                            message_text = f"Изображение сгенерировано по запросу: {query}"
                            
                            save_message(
                                session_id, 'assistant', message_text,
                                image_result['image_data'], image_result['file_type'],
                                image_result['file_name'], app.config['AUTOMATIC1111_MODEL'],
                                response_time={'mm_time': mm_time, 'gen_time': gen_time},
                                mm_time=str(mm_time), gen_time=str(gen_time),
                                mm_model=app.config['LLM_MULTIMODAL_MODEL'],
                                gen_model=app.config['AUTOMATIC1111_MODEL']
                            )
                            
                            return {
                                'response': message_text,
                                'session_id': session_id,
                                'model_used': app.config['AUTOMATIC1111_MODEL'],
                                'model_category': action_type,
                                'assistant_timestamp': completion_time_for_db,
                                'generated_image': image_result['image_data'],
                                'file_name': image_result['file_name'],
                                'file_size': image_result['file_size'],
                                'file_type': image_result['file_type'],
                                'mm_time': mm_time,
                                'gen_time': gen_time,
                                'mm_model': image_result['mm_model'],
                                'gen_model': image_result['gen_model'],
                                'response_time': {  # Возвращаем объект с раздельным временем
                                    'mm_time': mm_time,
                                    'gen_time': gen_time,
                                    'mm_model': image_result['mm_model'],
                                    'gen_model': image_result['gen_model']
                                },
                                'is_error': False
                            }
                        else:
                            final_response = f"⚠️ {image_result['error']}"
                            model_used = 'system'
                            is_error = True
                            process_time = mm_time + gen_time if 'gen_time' in locals() else mm_time
                else:
                    final_response = "⚠️ Модуль генерации изображений недоступен"
                    model_used = 'system'
                    is_error = True
                    process_time = 0
            
            elif action_type == 'camera':
                model_category = 'camera'
                if 'cam' in modules and modules['cam'].available:
                    # Замеряем время получения снимка
                    camera_start_time = time.time()
                    camera_result = modules['cam'].get_snapshot(query)
                    camera_time = round(time.time() - camera_start_time, 1)
                    
                    if camera_result['success']:
                        # Время ЗАВЕРШЕНИЯ получения снимка
                        completion_time_for_db = get_current_time_in_timezone_for_db()
                        
                        save_message(
                            session_id, 'assistant',
                            f"Изображение с камеры: {camera_result['room_name']}",
                            camera_result['image_data'], camera_result['image_type'],
                            camera_result['file_name'], model_used,
                            response_time=str(camera_time)
                        )
                        
                        return {
                            'response': f"Изображение с камеры: {camera_result['room_name']}",
                            'session_id': session_id,
                            'model_used': model_used,
                            'model_category': action_type,
                            'assistant_timestamp': completion_time_for_db,
                            'generated_image': camera_result['image_data'],
                            'file_name': camera_result['file_name'],
                            'file_size': camera_result['file_size'],
                            'file_type': camera_result['image_type'],
                            'response_time': camera_time,  # Время получения снимка
                            'is_error': False
                        }
                    else:
                        final_response = f"⚠️ {camera_result['error']}"
                        model_used = 'system'
                        is_error = True
                        process_time = camera_time
                else:
                    final_response = "⚠️ Модуль видеонаблюдения недоступен"
                    model_used = 'system'
                    is_error = True
                    process_time = 0
            
            elif action_type == 'reasoning':
                model_category = 'reasoning'
                if router_result.get('needs_reasoning'):
                    # Замеряем время работы reasoning модели
                    reasoning_start_time = time.time()
                    final_response = modules['base'].process_reasoning(query, current_time_str)
                    process_time = round(time.time() - reasoning_start_time, 1)
                    model_used = app.config['LLM_REASONING_MODEL']
                else:
                    process_time = 0
                    final_response = query
                is_error = False
            
            else:  # action_type == 'none'
                # Для простых ответов время = время работы маршрутизатора
                process_time = router_time
                final_response = query
                is_error = False
            
            # Определяем completion_time_for_db до проверки final_response
            completion_time_for_db = get_current_time_in_timezone_for_db()
            
            if final_response:
                # Сохраняем сообщение с временем обработки
                save_message(
                    session_id, 'assistant', final_response, 
                    model_name=model_used,
                    response_time=str(process_time)
                )
            
            return {
                'response': final_response,
                'session_id': session_id,
                'model_used': model_used,
                'model_category': model_category,
                'assistant_timestamp': completion_time_for_db,
                'response_time': process_time,  # Только время обработки моделью
                'is_error': is_error
            }
        
        # Для запросов с изображениями
        elif request_type == 'image' and file_data:
            # Замеряем время обработки изображения
            process_start_time = time.time()
            is_error = False
            
            if 'multimodal' in modules and modules['multimodal'].available:
                file_size = int((len(file_data) * 3) / 4) if file_data else 0
                is_valid, error = modules['multimodal'].validate_image(file_data, file_type, file_name, file_size)
                
                if is_valid:
                    bot_reply, error = modules['multimodal'].process_image_with_text(
                        file_data, message_text, current_time_str
                    )
                    process_time = round(time.time() - process_start_time, 1)
                    
                    if error:
                        bot_reply = f"⚠️ {error}"
                        is_error = True
                else:
                    bot_reply = f"⚠️ {error}"
                    process_time = round(time.time() - process_start_time, 1)
                    is_error = True
            else:
                bot_reply = "⚠️ Мультимодальная модель недоступна"
                process_time = round(time.time() - process_start_time, 1)
                is_error = True
            
            # Время ЗАВЕРШЕНИЯ обработки
            completion_time_for_db = get_current_time_in_timezone_for_db()
            
            # Сохраняем сообщение
            save_message(
                session_id, 'assistant', bot_reply, 
                model_name=app.config['LLM_MULTIMODAL_MODEL'] if 'multimodal' in modules else 'system',
                response_time=str(process_time)
            )
            
            return {
                'response': bot_reply,
                'session_id': session_id,
                'model_used': app.config['LLM_MULTIMODAL_MODEL'] if 'multimodal' in modules else 'system',
                'model_category': 'multimodal',
                'assistant_timestamp': completion_time_for_db,
                'response_time': process_time,  # Только время обработки моделью
                'is_error': is_error
            }
        
        # Для аудио запросов (не должны попадать сюда, но на всякий случай)
        elif request_type == 'audio' and file_data:
            app.logger.warning("Получен аудио запрос в очереди, но обработка не реализована")
            return {
                'error': 'Аудио запросы должны обрабатываться синхронно',
                'session_id': session_id,
                'is_error': True
            }
        
        # Время ЗАВЕРШЕНИЯ обработки для ошибки
        completion_time_for_db = get_current_time_in_timezone_for_db()
        return {
            'error': 'Неизвестный тип запроса',
            'session_id': session_id,
            'assistant_timestamp': completion_time_for_db,
            'is_error': True,
            'response_time': 0
        }
    
    def get_user_requests_status(self, user_id):
        """Получить статус всех запросов пользователя"""
        result = {
            'processing': None,
            'queued': [],
            'recent_completed': []
        }
        
        # Получаем все запросы пользователя
        user_requests = self.redis.smembers(f"{self.user_requests_key}:{user_id}")
        user_requests = {r.decode() if isinstance(r, bytes) else r for r in user_requests}
        
        # Проверяем обрабатываемые запросы
        processing_tasks = self.redis.hgetall(self.processing_key)
        for req_id, task_data in processing_tasks.items():
            req_id = req_id.decode() if isinstance(req_id, bytes) else req_id
            if req_id in user_requests:
                task = pickle.loads(task_data)
                # Добавляем информацию о статусе
                task_for_display = task.copy()
                task_for_display['status'] = 'processing'
                result['processing'] = self._format_request_info(task_for_display)
        
        # Получаем всю очередь
        queue_length = self.redis.llen(self.queue_key)
        queue_tasks = self.redis.lrange(self.queue_key, 0, queue_length - 1) if queue_length > 0 else []
        
        position = 1
        for task_data in queue_tasks:
            task = pickle.loads(task_data)
            if task['user_id'] == user_id:
                # Добавляем информацию о позиции
                task_for_display = task.copy()
                task_for_display['status'] = 'queued'
                task_for_display['position_info'] = {
                    'position': position,
                    'estimated_seconds': max(1, position * 5)
                }
                result['queued'].append(self._format_request_info(task_for_display))
            position += 1
        
        return result
    
    def _format_request_info(self, task):
        """Форматирование информации о запросе для UI"""
        request_type = task['data'].get('type', 'unknown')
        type_icons = {
            'text': '💬',
            'image': '🎨',
            'camera': '📷',
            'reasoning': '🧠',
            'audio': '🎤'
        }
        
        return {
            'id': task['id'],
            'session_id': task['session_id'],
            'session_title': task.get('session_title', 'Неизвестный сеанс'),
            'type': request_type,
            'type_icon': type_icons.get(request_type, '📄'),
            'status': task.get('status', 'queued'),
            'position_info': task.get('position_info', {'position': '?', 'estimated_seconds': 5}),
            'preview': task['data'].get('preview', '')
        }
    
    def cancel_request(self, user_id, request_id):
        """Отмена запроса"""
        return False
    
    def check_result(self, request_id):
        """Проверить результат запроса"""
        result_data = self.redis.hget(self.results_key, request_id)
        if result_data:
            return pickle.loads(result_data)
        return None

# Глобальный экземпляр очереди на Redis
request_queue = RedisRequestQueue(app.config['REDIS_URL'])

# -------------------------------
# Функции для работы с БД (вспомогательные)
# -------------------------------
def get_user_sessions(user_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Получаем сеансы
        c.execute('''
            SELECT id, title, model_name, created_at, updated_at
            FROM chat_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
        ''', (user_id,))
        sessions = [dict(row) for row in c.fetchall()]
        
        # Для каждого сеанса проверяем наличие непрочитанных сообщений ассистента
        for s in sessions:
            c.execute('''
                SELECT last_visit FROM session_visits
                WHERE user_id = ? AND session_id = ?
            ''', (user_id, s['id']))
            row = c.fetchone()
            last_visit = row[0] if row else '1970-01-01 00:00:00'
            
            c.execute('''
                SELECT COUNT(*) FROM messages
                WHERE session_id = ? AND role = 'assistant' AND timestamp > ?
            ''', (s['id'], last_visit))
            count = c.fetchone()[0]
            s['has_unread'] = count > 0
        
        return sessions

def get_session_messages(session_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute('''
            SELECT role, content, file_data, file_type, file_name, 
                   timestamp, model_name, response_time, mm_time, gen_time,
                   mm_model, gen_model
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
        ''', (session_id,))
        
        messages = []
        for row in c.fetchall():
            msg_dict = dict(row)
            
            # Парсим response_time, если это JSON
            if msg_dict.get('response_time'):
                try:
                    msg_dict['response_time'] = json.loads(msg_dict['response_time'])
                except:
                    # Если не JSON, оставляем как есть (число или строка)
                    pass
            
            # Преобразуем timestamp в ISO формат для JS
            if msg_dict.get('timestamp'):
                try:
                    dt = datetime.strptime(msg_dict['timestamp'], '%Y-%m-%d %H:%M:%S')
                    if app.config.get('TIMEZONE'):
                        dt = app.config['TIMEZONE'].localize(dt)
                    msg_dict['timestamp'] = dt.isoformat()
                except Exception as e:
                    app.logger.error(f"Ошибка преобразования timestamp: {str(e)}")
            
            messages.append(msg_dict)
        
        return messages

def create_session(user_id, title="Новый сеанс"):
    session_id = str(uuid.uuid4())
    current_time = get_current_time_in_timezone_for_db()
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO chat_sessions (id, user_id, title, model_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (session_id, user_id, title, 'auto', current_time, current_time))
        
        # Сразу создаём запись о посещении
        c.execute('''
            INSERT OR REPLACE INTO session_visits (user_id, session_id, last_visit)
            VALUES (?, ?, ?)
        ''', (user_id, session_id, current_time))
        
        conn.commit()
    return session_id

def update_session_title(session_id, first_message, file_name=None):
    if first_message and first_message.strip():
        title = first_message[:40] + ('...' if len(first_message) > 40 else '')
    elif file_name:
        title = file_name[:40] + ('...' if len(file_name) > 40 else '')
    else:
        title = "Новый сеанс"
    
    current_time = get_current_time_in_timezone_for_db()
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE chat_sessions
            SET title = ?, updated_at = ?
            WHERE id = ?
        ''', (title, current_time, session_id))
        conn.commit()
    
    return title

def save_message(session_id, role, content, file_data=None, file_type=None, file_name=None, 
                 model_name=None, response_time=None, mm_time=None, gen_time=None, 
                 mm_model=None, gen_model=None):
    """Сохранение сообщения с дополнительными полями для времени ответа"""
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        
        current_time = get_current_time_in_timezone_for_db()
        
        # Преобразуем сложные объекты в JSON для хранения
        if response_time and isinstance(response_time, dict):
            response_time = json.dumps(response_time, ensure_ascii=False)
        elif response_time is not None and not isinstance(response_time, str):
            # Преобразуем число в строку для хранения
            response_time = str(response_time)
        
        c.execute('''
            INSERT INTO messages (
                session_id, role, content, file_data, file_type, file_name, 
                model_name, timestamp, response_time, mm_time, gen_time, 
                mm_model, gen_model
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id, role, content, file_data, file_type, file_name, 
            model_name, current_time, response_time, mm_time, gen_time,
            mm_model, gen_model
        ))
        
        c.execute('''
            UPDATE chat_sessions
            SET updated_at = ?
            WHERE id = ?
        ''', (current_time, session_id))
        conn.commit()

def get_last_session(user_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        return row[0] if row else None

def set_last_session(user_id, session_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO user_sessions (user_id, last_session_id)
            VALUES (?, ?)
        ''', (user_id, session_id))
        conn.commit()

# -------------------------------
# Маршруты аутентификации
# -------------------------------
@app.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('chat'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not email or not password:
            return render_template('login.html', error='Все поля обязательны')
        
        if not USERS:
            return render_template('login.html', error='Ошибка конфигурации сервера')
        
        if email in USERS and USERS[email]['password'] == password:
            session['email'] = email
            session['service_class'] = USERS[email]['service_class']
            return redirect(url_for('chat'))
        else:
            return render_template('login.html', error='Неверный email или пароль')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'email' in session and 'current_session' in session:
        set_last_session(session['email'], session['current_session'])
    session.clear()
    return redirect(url_for('login'))

# -------------------------------
# Основной чат
# -------------------------------
@app.route('/chat')
def chat():
    if 'email' not in session:
        return redirect(url_for('login'))
    
    user_id = session['email']
    sessions = get_user_sessions(user_id)
    
    if not session.get('current_session'):
        last_id = get_last_session(user_id)
        if last_id and any(s['id'] == last_id for s in sessions):
            session['current_session'] = last_id
        elif sessions:
            session['current_session'] = sessions[0]['id']
        else:
            new_id = create_session(user_id)
            session['current_session'] = new_id
            sessions = get_user_sessions(user_id)
    
    return render_template('chat.html', 
                         sessions=sessions, 
                         current_session=session.get('current_session'),
                         footer_text=app.config.get('FOOTER_TEXT', ""))

# -------------------------------
# API для работы с сеансами
# -------------------------------
@app.route('/api/sessions', methods=['GET'])
def api_get_sessions():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    return jsonify(get_user_sessions(session['email']))

@app.route('/api/sessions/<session_id>/messages', methods=['GET'])
def api_get_messages(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    return jsonify(get_session_messages(session_id))

@app.route('/api/sessions/<session_id>/switch', methods=['POST'])
def api_switch_session(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user_id = session['email']
    session['current_session'] = session_id
    set_last_session(user_id, session_id)
    
    # Обновляем время последнего посещения
    current_time = get_current_time_in_timezone_for_db()
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO session_visits (user_id, session_id, last_visit)
            VALUES (?, ?, ?)
        ''', (user_id, session_id, current_time))
        conn.commit()
    
    return jsonify({'status': 'ok'})

@app.route('/api/sessions/<session_id>/model-info', methods=['GET'])
def api_get_session_model(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT model_name FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        return jsonify({'model_name': row[0] if row else 'auto'})

@app.route('/api/sessions/new', methods=['POST'])
def api_new_session():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    session_id = create_session(session['email'])
    session['current_session'] = session_id
    set_last_session(session['email'], session_id)
    return jsonify({'id': session_id, 'title': 'Новый сеанс'})

@app.route('/api/sessions/<session_id>/update-title', methods=['POST'])
def api_update_session_title(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    data = request.get_json()
    new_title = data.get('title', 'Новый сеанс')
    current_time = get_current_time_in_timezone_for_db()
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE chat_sessions
            SET title = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
        ''', (new_title, current_time, session_id, session['email']))
        conn.commit()
    
    return jsonify({'status': 'ok', 'title': new_title})

@app.route('/api/sessions/<session_id>/delete', methods=['POST'])
def api_delete_session(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT user_id FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        
        if not row or row[0] != user_id:
            return jsonify({'error': 'Нет прав'}), 403
        
        c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
        c.execute('DELETE FROM chat_sessions WHERE id = ?', (session_id,))
        
        c.execute('SELECT COUNT(*) FROM chat_sessions WHERE user_id = ?', (user_id,))
        count = c.fetchone()[0]
        
        if count == 0:
            c.execute('DELETE FROM user_sessions WHERE user_id = ?', (user_id,))
        else:
            c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = ?', (user_id,))
            row = c.fetchone()
            if row and row[0] == session_id:
                c.execute('SELECT id FROM chat_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1', (user_id,))
                new_last = c.fetchone()
                if new_last:
                    c.execute('UPDATE user_sessions SET last_session_id = ? WHERE user_id = ?', (new_last[0], user_id))
                else:
                    c.execute('DELETE FROM user_sessions WHERE user_id = ?', (user_id,))
        
        # Удаляем также записи о посещениях
        c.execute('DELETE FROM session_visits WHERE session_id = ?', (session_id,))
        
        conn.commit()
    
    if session.get('current_session') == session_id:
        session.pop('current_session', None)
    
    return jsonify({'status': 'ok'})

@app.route('/api/sessions/<session_id>/visit', methods=['POST'])
def api_update_session_visit(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    current_time = get_current_time_in_timezone_for_db()
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO session_visits (user_id, session_id, last_visit)
            VALUES (?, ?, ?)
        ''', (user_id, session_id, current_time))
        conn.commit()
    
    return jsonify({'status': 'ok'})

# -------------------------------
# API для очереди запросов (Redis)
# -------------------------------
@app.route('/api/queue/status', methods=['GET'])
def api_queue_status():
    """Получить статус всех запросов текущего пользователя"""
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    status = request_queue.get_user_requests_status(user_id)
    
    # Добавляем общую информацию о системе
    queue_length = request_queue.redis.llen(request_queue.queue_key)
    status['system'] = {
        'total_queued': queue_length,
        'current_load': 'high' if queue_length > 10 else 'normal',
        'avg_response_time': 5  # Заглушка
    }
    
    return jsonify(status)

@app.route('/api/queue/counts', methods=['GET'])
def api_queue_counts():
    """Возвращает количество запросов текущего пользователя в очереди и общее количество"""
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user_id = session['email']
    user_queued, total_queued = request_queue.get_user_queue_counts(user_id)
    return jsonify({
        'user_queued': user_queued,
        'total_queued': total_queued
    })

@app.route('/api/queue/cancel/<request_id>', methods=['POST'])
def api_cancel_request(request_id):
    """Отмена запроса"""
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    success = request_queue.cancel_request(session['email'], request_id)
    return jsonify({'success': success})

@app.route('/api/queue/result/<request_id>', methods=['GET'])
def api_check_result(request_id):
    """Проверить результат запроса"""
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    result = request_queue.check_result(request_id)
    if result:
        return jsonify(result)
    else:
        return jsonify({'status': 'pending'})

# -------------------------------
# API для получения подписи футера
# -------------------------------
@app.route('/api/footer-text', methods=['GET'])
def api_footer_text():
    return app.config.get('FOOTER_TEXT', "Подпись не настроена")

# -------------------------------
# Очистка истории сеанса
# -------------------------------
@app.route('/clear_history', methods=['POST'])
def clear_history():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    session_id = session.get('current_session')
    if not session_id:
        return jsonify({'error': 'Нет активного сеанса'}), 400
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
        current_time = get_current_time_in_timezone_for_db()
        c.execute('UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?', 
                 ('Новый сеанс', current_time, session_id))
        conn.commit()
    
    return jsonify({'status': 'ok'})

# -------------------------------
# ОТПРАВКА СООБЩЕНИЯ (ОСНОВНАЯ) - ИЗМЕНЕНО
# -------------------------------
@app.route('/send_message', methods=['POST'])
def send_message():
    app.logger.info("=" * 50)
    app.logger.info("send_message: НАЧАЛО ОБРАБОТКИ ЗАПРОСА")
    
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    user_class = USERS.get(user_id, {}).get('service_class', 2)
    session_id = session.get('current_session')
    
    if not session_id:
        session_id = create_session(user_id)
        session['current_session'] = session_id
    
    # Получаем данные из запроса
    message_text = ""
    file_data = None
    file_type = None
    file_name = None
    voice_record = False  # Флаг голосового сообщения (запись с микрофона)
    
    if request.content_type and 'multipart/form-data' in request.content_type:
        message_text = request.form.get('message', '')
        
        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename:
                file_data = base64.b64encode(file.read()).decode('utf-8')
                file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
                file_name = file.filename
        
        # ИЗМЕНЕНИЕ: проверяем наличие флага голосового сообщения
        voice_record = request.form.get('voice_record') == 'true'
    else:
        try:
            data = request.get_json()
            if data:
                message_text = data.get('message', '')
        except:
            message_text = request.form.get('message', '')
    
    if not message_text and not file_data:
        return jsonify({'error': 'Пустое сообщение'}), 400
    
    # Определяем тип запроса
    request_type = 'text'
    if file_data and file_type:
        if file_type.startswith('image/'):
            request_type = 'image'
        elif modules['audio'].is_audio_file(file_type, file_name):
            request_type = 'audio'
    
    # Формируем содержимое пользовательского сообщения
    user_content = []
    if message_text:
        user_content.append({"type": "text", "text": message_text})
    
    if file_data:
        # Определяем тип вложения
        if file_type and file_type.startswith('image/'):
            content_type = "image"
        elif file_type and modules['audio'].is_audio_file(file_type, file_name):
            content_type = "audio"
        else:
            content_type = "file"
        
        user_content.append({
            "type": content_type,
            "file_data": file_data,
            "file_type": file_type,
            "file_name": file_name
        })
    
    user_content_json = json.dumps(user_content, ensure_ascii=False)
    
    # Сохраняем сообщение пользователя (сначала!)
    save_message(session_id, 'user', user_content_json, file_data, file_type, file_name, None)
    
    # Проверяем, первое ли это сообщение
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
        message_count = c.fetchone()[0]
        is_first_message = message_count == 1  # Только что сохранили пользовательское
    
    if is_first_message:
        update_session_title(session_id, message_text, file_name)
    
    # Если это аудио, выполняем транскрибацию
    transcribed_text = None
    if request_type == 'audio':
        app.logger.info("send_message: обнаружено аудио, запуск транскрибации")
        transcribed_text = modules['audio'].transcribe(file_data, file_type, file_name)
        
        if transcribed_text is None:
            return jsonify({'error': 'Не удалось распознать речь'}), 500
        
        app.logger.info(f"send_message: транскрибация успешна: {transcribed_text[:100]}")
        
        # Сохраняем системное сообщение с транскрипцией (после пользовательского)
        system_content = f"🎤 Распознано: {transcribed_text}"
        save_message(session_id, 'assistant', system_content, 
                     model_name='whisper', response_time=None)
        
        # ИЗМЕНЕНИЕ: различаем голосовое сообщение и загруженный аудиофайл
        if voice_record:
            # Это голосовое сообщение (запись с микрофона) – отправляем распознанный текст в очередь
            app.logger.info("send_message: голосовое сообщение, ставим задачу в очередь с текстом транскрипции")
            
            # Формируем данные для очереди как текстовый запрос
            request_data = {
                'type': 'text',
                'text': transcribed_text,
                'preview': (transcribed_text[:50] + '...') if transcribed_text else 'Голосовой запрос'
            }
            
            # Добавляем в очередь Redis
            request_id, position_info = request_queue.add_request(
                user_id, session_id, request_data, user_class
            )
            
            return jsonify({
                'status': 'queued',
                'transcribed_text': transcribed_text,
                'session_id': session_id,
                'request_id': request_id,
                'position': position_info['position'],
                'estimated_wait': position_info['estimated_seconds'],
                'message': f'Голос распознан, запрос поставлен в очередь (позиция {position_info["position"]})'
            })
        else:
            # Это загруженный аудиофайл – только транскрибация, без дальнейших действий
            return jsonify({
                'status': 'success',
                'transcribed_text': transcribed_text,
                'session_id': session_id,
                'message': 'Аудио распознано'
            })
    
    # Для изображений и текста (без аудио) – ставим в очередь
    # Формируем данные для очереди
    if request_type == 'image' and file_data:
        # Для изображений передаём файл
        request_data = {
            'type': 'image',
            'text': message_text,
            'file_data': file_data,
            'file_type': file_type,
            'file_name': file_name,
            'preview': (message_text[:50] + '...') if message_text else (file_name or 'Изображение')
        }
    else:
        # Текстовый запрос (без файла или с файлом, который не аудио и не изображение – например, документ, но пока не обрабатываем)
        request_data = {
            'type': 'text',
            'text': message_text,
            'preview': (message_text[:50] + '...') if message_text else 'Текстовый запрос'
        }
    
    # Добавляем в очередь Redis
    request_id, position_info = request_queue.add_request(
        user_id, session_id, request_data, user_class
    )
    
    return jsonify({
        'status': 'queued',
        'request_id': request_id,
        'position': position_info['position'],
        'estimated_wait': position_info['estimated_seconds'],
        'message': f'Запрос поставлен в очередь (позиция {position_info["position"]})'
    })

# -------------------------------
# Статика и прочее
# -------------------------------
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.context_processor
def inject_footer():
    return {'footer_content': app.config.get('FOOTER_TEXT', "")}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)