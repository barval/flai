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
import heapq
from collections import defaultdict
import threading

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
    'MAX_IMAGE_SIZE_MB': int(os.getenv('MAX_IMAGE_SIZE_MB', 5))
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

# Модули-заглушки
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

init_db()

# -------------------------------
# Функции для работы с пользователями (обновленные с классами обслуживания)
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
# Класс PriorityRequestQueue (менеджер очереди с приоритетами)
# -------------------------------
class PriorityRequestQueue:
    def __init__(self):
        # Используем heapq для эффективной priority queue
        # Элемент: (priority_class, timestamp, request_id)
        self.queues = {
            0: [],  # высший приоритет
            1: [],  # средний
            2: []   # низший
        }
        self.current_request = None  # Текущий выполняемый запрос
        self.lock = threading.Lock()
        self.processing = False
        self.request_history = []  # История для оценки времени
        
        # Маппинг request_id -> информация о запросе для быстрого доступа
        self.requests_map = {}
        
        # Статистика по пользователям
        self.user_stats = defaultdict(lambda: {
            'total_requests': 0,
            'completed_requests': 0,
            'total_wait_time': 0,
            'last_request_time': None
        })
        
        # Словарь для хранения результатов (для long-polling)
        self.results = {}
        
    def _get_session_title(self, session_id):
        """Получить заголовок сеанса по ID"""
        try:
            with sqlite3.connect(CHAT_DB_PATH) as conn:
                c = conn.cursor()
                c.execute('SELECT title FROM chat_sessions WHERE id = ?', (session_id,))
                row = c.fetchone()
                return row[0] if row else "Неизвестный сеанс"
        except:
            return "Неизвестный сеанс"
    
    def add_request(self, user_id, session_id, request_data, user_class):
        """
        Добавление запроса в очередь
        Возвращает request_id и информацию о позиции
        """
        request_id = str(uuid.uuid4())
        timestamp = time.time()
        
        # Получаем статистику пользователя для оценки
        stats = self.user_stats[user_id]
        stats['total_requests'] += 1
        stats['last_request_time'] = datetime.now()
        
        request_info = {
            'id': request_id,
            'user_id': user_id,
            'session_id': session_id,
            'data': request_data,
            'timestamp': timestamp,
            'user_class': user_class,
            'status': 'queued',  # queued, processing, completed, cancelled
            'position_info': None,  # Будет заполнено позже
            'estimated_wait': None,
            'start_time': None,
            'end_time': None,
            'session_title': self._get_session_title(session_id)
        }
        
        with self.lock:
            # Добавляем в соответствующую очередь
            # heapq использует кортеж (приоритет, timestamp, request_id) для сортировки
            # Чем меньше приоритет, тем выше класс (0 - высший)
            heapq.heappush(self.queues[user_class], 
                          (user_class, timestamp, request_id))
            
            self.requests_map[request_id] = request_info
            
            # Рассчитываем позицию и ожидание
            position_info = self._calculate_position(request_id)
            request_info['position_info'] = position_info
            
            # Запускаем обработчик, если не запущен
            if not self.processing:
                threading.Thread(target=self._process_queue, daemon=True).start()
        
        return request_id, position_info
    
    def _calculate_position(self, request_id):
        """Рассчитывает позицию запроса в очереди"""
        request = self.requests_map.get(request_id)
        if not request:
            return None
            
        user_class = request['user_class']
        
        with self.lock:
            # Считаем сколько запросов впереди в том же классе
            same_class_before = 0
            found = False
            for item in self.queues[user_class]:
                cls, ts, rid = item
                if rid == request_id:
                    found = True
                    break
                if ts < request['timestamp']:
                    same_class_before += 1
            
            if not found:
                # Запрос уже не в очереди (возможно, обрабатывается)
                return {
                    'total_before': 0,
                    'same_class_before': 0,
                    'higher_class_before': 0,
                    'estimated_seconds': 0,
                    'position': 0
                }
            
            # Считаем запросы в более высоких классах
            higher_class_total = 0
            for cls in range(user_class):  # 0, 1 (если user_class=2)
                higher_class_total += len(self.queues[cls])
            
            total_before = higher_class_total + same_class_before
            
            # Оценка времени ожидания
            estimated_wait = self._estimate_wait_time(total_before, user_class)
            
            return {
                'total_before': total_before,
                'same_class_before': same_class_before,
                'higher_class_before': higher_class_total,
                'estimated_seconds': estimated_wait,
                'position': total_before + 1  # 1-based позиция
            }
    
    def _estimate_wait_time(self, position, user_class):
        """Оценка времени ожидания на основе истории и класса"""
        if position == 0:
            return 0
            
        if not self.request_history:
            # Дефолтные значения, если нет истории
            return position * 5  # грубая оценка
        
        # Анализируем историю по классам (последние 50 запросов)
        recent_history = self.request_history[-50:]
        
        # Группируем по классам
        class_durations = {0: [], 1: [], 2: []}
        for h in recent_history:
            cls = h['request']['user_class']
            class_durations[cls].append(h['duration'])
        
        # Средняя длительность для каждого класса
        avg_durations = {}
        for cls, durations in class_durations.items():
            if durations:
                avg_durations[cls] = sum(durations) / len(durations)
            else:
                avg_durations[cls] = 5  # запасное значение
        
        # Оцениваем: сначала все запросы высших классов, потом наши
        wait_time = 0
        remaining = position
        
        with self.lock:
            for cls in range(3):
                if cls < user_class:
                    # Все запросы высших классов
                    wait_time += len(self.queues[cls]) * avg_durations.get(cls, 5)
                elif cls == user_class:
                    # Наши запросы до текущего
                    wait_time += (remaining - 1) * avg_durations.get(cls, 5)
                    break
        
        return round(wait_time, 1)
    
    def _process_queue(self):
        """Основной обработчик очереди"""
        with self.lock:
            self.processing = True
        
        while True:
            next_request = None
            next_class = None
            
            with self.lock:
                # Ищем запрос в классах по приоритету
                for cls in [0, 1, 2]:
                    if self.queues[cls]:
                        # Берем самый старый (heap гарантирует порядок)
                        cls_val, ts, rid = self.queues[cls][0]
                        next_request = self.requests_map.get(rid)
                        if next_request and next_request['status'] == 'queued':
                            next_class = cls
                            # Удаляем из очереди
                            heapq.heappop(self.queues[cls])
                            break
                        else:
                            # Запрос отменён или уже обработан - просто удаляем
                            heapq.heappop(self.queues[cls])
                            continue
            
            if not next_request:
                # Очередь пуста
                with self.lock:
                    self.processing = False
                break
            
            # Обновляем статус
            next_request['status'] = 'processing'
            next_request['start_time'] = time.time()
            self.current_request = next_request
            
            # Вычисляем время ожидания в очереди
            wait_time = next_request['start_time'] - next_request['timestamp']
            
            # Обрабатываем запрос (без лока, чтобы можно было добавлять новые)
            try:
                # Вызываем реальную обработку
                result = self._process_request_impl(next_request)
                
                # Сохраняем результат
                self.results[next_request['id']] = {
                    'status': 'completed',
                    'result': result,
                    'timestamp': time.time()
                }
                
            except Exception as e:
                app.logger.error(f"Ошибка обработки запроса {next_request['id']}: {str(e)}")
                self.results[next_request['id']] = {
                    'status': 'error',
                    'error': str(e),
                    'timestamp': time.time()
                }
            
            # Обновляем статистику
            end_time = time.time()
            next_request['status'] = 'completed'
            next_request['end_time'] = end_time
            duration = end_time - next_request['start_time']
            
            # Сохраняем в историю
            self.request_history.append({
                'request': next_request,
                'wait_time': wait_time,
                'duration': duration,
                'completed': end_time
            })
            
            # Обновляем статистику пользователя
            stats = self.user_stats[next_request['user_id']]
            stats['completed_requests'] += 1
            stats['total_wait_time'] += wait_time
            
            self.current_request = None
    
    def _process_request_impl(self, request):
        """
        Реальная обработка запроса
        Здесь вызываются соответствующие модули в зависимости от типа запроса
        """
        user_id = request['user_id']
        session_id = request['session_id']
        request_data = request['data']
        
        # Получаем текущее время
        current_time_str = get_current_time_in_timezone()
        current_time_for_db = get_current_time_in_timezone_for_db()
        
        # Определяем тип запроса и обрабатываем
        request_type = request_data.get('type', 'text')
        message_text = request_data.get('text', '')
        file_data = request_data.get('file_data')
        file_type = request_data.get('file_type')
        file_name = request_data.get('file_name')
        
        # Для текстовых запросов используем базовый модуль
        if request_type == 'text':
            # Обрабатываем через базовый модуль
            router_result = modules['base'].process_message(message_text, current_time_str)
            
            if 'error' in router_result:
                return {'error': router_result['error']}
            
            action_type = router_result['action']
            query = router_result['query']
            
            final_response = ""
            model_used = app.config['LLM_CHAT_MODEL']
            model_category = 'chat'  # По умолчанию
            
            # Обработка в зависимости от типа действия
            if action_type == 'image':
                # Запрос на создание изображения
                model_category = 'image'
                if 'image' in modules and modules['image'].available and 'multimodal' in modules:
                    app.logger.info("Обработка запроса на создание изображения")
                    
                    image_result = modules['image'].generate_image(query)
                    
                    if image_result['success']:
                        message_text = f"Изображение сгенерировано моделью {app.config['AUTOMATIC1111_MODEL']} по запросу: {query}"
                        
                        save_message(
                            session_id, 'assistant', message_text,
                            image_result['image_data'], image_result['file_type'],
                            image_result['file_name'], app.config['LLM_MULTIMODAL_MODEL']
                        )
                        
                        return {
                            'response': message_text,
                            'session_id': session_id,
                            'model_used': app.config['LLM_MULTIMODAL_MODEL'],
                            'model_category': action_type,
                            'assistant_timestamp': current_time_for_db,
                            'generated_image': image_result['image_data'],
                            'file_name': image_result['file_name'],
                            'file_size': image_result['file_size'],
                            'file_type': image_result['file_type']
                        }
                    else:
                        final_response = f"⚠️ {image_result['error']}"
                else:
                    final_response = "⚠️ Модуль генерации изображений недоступен"
            
            elif action_type == 'camera':
                # Запрос к камере
                model_category = 'camera'
                if 'cam' in modules and modules['cam'].available:
                    app.logger.info("Обработка запроса к камере")
                    
                    camera_result = modules['cam'].get_snapshot(query)
                    
                    if camera_result['success']:
                        save_message(
                            session_id, 'assistant',
                            f"Изображение с камеры: {camera_result['room_name']}",
                            camera_result['image_data'], camera_result['image_type'],
                            camera_result['file_name'], model_used
                        )
                        
                        return {
                            'response': f"Изображение с камеры: {camera_result['room_name']}",
                            'session_id': session_id,
                            'model_used': model_used,
                            'model_category': action_type,
                            'assistant_timestamp': current_time_for_db,
                            'generated_image': camera_result['image_data'],
                            'file_name': camera_result['file_name'],
                            'file_size': camera_result['file_size'],
                            'file_type': camera_result['image_type']
                        }
                    else:
                        final_response = f"⚠️ {camera_result['error']}"
                else:
                    final_response = "⚠️ Модуль видеонаблюдения недоступен"
            
            elif action_type == 'reasoning':
                # Сложный запрос
                model_category = 'reasoning'
                if router_result.get('needs_reasoning'):
                    app.logger.info("Обработка сложного запроса через reasoning модель")
                    final_response = modules['base'].process_reasoning(query, current_time_str)
                    model_used = app.config['LLM_REASONING_MODEL']
                else:
                    final_response = query
            
            else:  # action_type == 'none'
                final_response = query
            
            # Сохраняем ответ
            if final_response:
                save_message(session_id, 'assistant', final_response, model_name=model_used)
            
            return {
                'response': final_response,
                'session_id': session_id,
                'model_used': model_used,
                'model_category': model_category,
                'assistant_timestamp': current_time_for_db
            }
        
        # Для запросов с изображениями
        elif request_type == 'image' and file_data:
            if 'multimodal' in modules and modules['multimodal'].available:
                # Проверяем валидность изображения
                # Приблизительный размер файла
                file_size = int((len(file_data) * 3) / 4) if file_data else 0
                is_valid, error = modules['multimodal'].validate_image(file_data, file_type, file_name, file_size)
                
                if is_valid:
                    # Обрабатываем изображение через мультимодальную модель
                    bot_reply, error = modules['multimodal'].process_image_with_text(
                        file_data, message_text, current_time_str
                    )
                    
                    if error:
                        bot_reply = f"⚠️ {error}"
                    
                    save_message(session_id, 'assistant', bot_reply, model_name=app.config['LLM_MULTIMODAL_MODEL'])
                    
                    return {
                        'response': bot_reply,
                        'session_id': session_id,
                        'model_used': app.config['LLM_MULTIMODAL_MODEL'],
                        'model_category': 'multimodal',
                        'assistant_timestamp': current_time_for_db
                    }
                else:
                    bot_reply = f"⚠️ {error}"
                    save_message(session_id, 'assistant', bot_reply, model_name='system')
                    
                    return {
                        'response': bot_reply,
                        'session_id': session_id,
                        'model_used': 'system',
                        'assistant_timestamp': current_time_for_db
                    }
            else:
                bot_reply = "⚠️ Мультимодальная модель недоступна. Файлы не поддерживаются."
                save_message(session_id, 'assistant', bot_reply, model_name='system')
                
                return {
                    'response': bot_reply,
                    'session_id': session_id,
                    'model_used': 'system',
                    'assistant_timestamp': current_time_for_db
                }
        
        return {'error': 'Неизвестный тип запроса'}
    
    def get_user_requests_status(self, user_id):
        """Получить статус всех запросов пользователя"""
        result = {
            'processing': None,
            'queued': [],
            'recent_completed': []
        }
        
        # Текущий обрабатываемый запрос
        if self.current_request and self.current_request['user_id'] == user_id:
            result['processing'] = self._format_request_info(self.current_request)
        
        # Запросы в очереди
        with self.lock:
            for cls in [0, 1, 2]:
                for item in self.queues[cls]:
                    cls_val, ts, rid = item
                    req = self.requests_map.get(rid)
                    if req and req['user_id'] == user_id and req['status'] == 'queued':
                        # Обновляем позицию перед отправкой
                        req['position_info'] = self._calculate_position(rid)
                        result['queued'].append(self._format_request_info(req))
        
        # Недавно завершённые (из истории)
        for h in reversed(self.request_history[-10:]):
            if h['request']['user_id'] == user_id:
                result['recent_completed'].append({
                    'session_title': h['request']['session_title'],
                    'completed_at': datetime.fromtimestamp(h['completed']).strftime('%H:%M:%S'),
                    'duration': round(h['duration'], 1)
                })
        
        # Сортируем очередь по ожидаемому времени
        result['queued'].sort(key=lambda x: x['position_info']['position'])
        
        return result
    
    def _format_request_info(self, request):
        """Форматирование информации о запросе для UI"""
        request_type = request['data'].get('type', 'unknown')
        type_icons = {
            'text': '💬',
            'image': '🎨',
            'camera': '📷',
            'reasoning': '🧠'
        }
        
        return {
            'id': request['id'],
            'session_id': request['session_id'],
            'session_title': request['session_title'],
            'type': request_type,
            'type_icon': type_icons.get(request_type, '📄'),
            'status': request['status'],
            'position_info': request['position_info'],
            'wait_time': round(time.time() - request['timestamp'], 1) if request['status'] == 'queued' else None,
            'preview': request['data'].get('preview', '')
        }
    
    def cancel_request(self, user_id, request_id):
        """Отмена запроса (только для своего пользователя)"""
        with self.lock:
            request = self.requests_map.get(request_id)
            if not request or request['user_id'] != user_id:
                return False
            
            if request['status'] == 'queued':
                request['status'] = 'cancelled'
                return True
            elif request['status'] == 'processing':
                # Не можем отменить выполняющийся запрос
                return False
            
        return False
    
    def get_average_response_time(self):
        """Получить среднее время ответа"""
        if not self.request_history:
            return 0
        recent = self.request_history[-50:]
        return round(sum(h['duration'] for h in recent) / len(recent), 1)
    
    def check_result(self, request_id):
        """Проверить, готов ли результат для request_id"""
        return self.results.get(request_id)

# Глобальный экземпляр очереди
request_queue = PriorityRequestQueue()

# -------------------------------
# Функции для работы с БД (вспомогательные)
# -------------------------------
def get_user_sessions(user_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT id, title, model_name, created_at, updated_at
            FROM chat_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
        ''', (user_id,))
        return [dict(row) for row in c.fetchall()]

def get_session_messages(session_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute('''
            SELECT role, content, file_data, file_type, file_name, timestamp, model_name
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
        ''', (session_id,))
        
        messages = []
        for row in c.fetchall():
            msg_dict = dict(row)
            if msg_dict.get('timestamp') and app.config.get('TIMEZONE'):
                try:
                    dt = datetime.strptime(msg_dict['timestamp'], '%Y-%m-%d %H:%M:%S')
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

def save_message(session_id, role, content, file_data=None, file_type=None, file_name=None, model_name=None):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        
        current_time = get_current_time_in_timezone_for_db()
        
        c.execute('''
            INSERT INTO messages (session_id, role, content, file_data, file_type, file_name, model_name, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (session_id, role, content, file_data, file_type, file_name, model_name, current_time))
        
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
    session['current_session'] = session_id
    set_last_session(session['email'], session_id)
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
        
        conn.commit()
    
    if session.get('current_session') == session_id:
        session.pop('current_session', None)
    
    return jsonify({'status': 'ok'})

# -------------------------------
# API для очереди запросов
# -------------------------------
@app.route('/api/queue/status', methods=['GET'])
def api_queue_status():
    """Получить статус всех запросов текущего пользователя"""
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    status = request_queue.get_user_requests_status(user_id)
    
    # Добавляем общую информацию о системе
    total_queued = sum(len(q) for q in request_queue.queues.values())
    status['system'] = {
        'total_queued': total_queued,
        'current_load': 'high' if total_queued > 10 else 'normal',
        'avg_response_time': request_queue.get_average_response_time()
    }
    
    return jsonify(status)

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
# ОТПРАВКА СООБЩЕНИЯ (исправленная версия)
# -------------------------------
@app.route('/send_message', methods=['POST'])
def send_message():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    # Проверяем базовый модуль
    if not modules['base'].available:
        return jsonify({'error': 'Базовый сервис чата недоступен'}), 500
    
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
    
    # Проверяем тип контента
    if request.content_type and 'multipart/form-data' in request.content_type:
        # Это multipart/form-data (с файлом)
        message_text = request.form.get('message', '')
        
        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename:
                file_data = base64.b64encode(file.read()).decode('utf-8')
                file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
                file_name = file.filename
    else:
        # Это application/json (без файла)
        try:
            data = request.get_json()
            if data:
                message_text = data.get('message', '')
        except:
            # Если не JSON и не multipart, пробуем как обычную форму
            message_text = request.form.get('message', '')
    
    # Получаем текущее время
    current_time_for_db = get_current_time_in_timezone_for_db()
    
    # Проверяем, первое ли это сообщение
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
        is_first_message = c.fetchone()[0] == 0
    
    # Сохраняем сообщение пользователя
    user_content = []
    if message_text:
        user_content.append({"type": "text", "text": message_text})
    
    if file_data:
        user_content.append({
            "type": "file", 
            "file_data": file_data, 
            "file_type": file_type, 
            "file_name": file_name
        })
    
    save_message(session_id, 'user', json.dumps(user_content, ensure_ascii=False), 
                file_data, file_type, file_name, None)
    
    if is_first_message:
        update_session_title(session_id, message_text, file_name)
    
    # Определяем тип запроса для статистики
    request_type = 'text'
    if file_data and file_type and file_type.startswith('image/'):
        request_type = 'image'
    
    # Создаём данные для очереди
    request_data = {
        'type': request_type,
        'text': message_text,
        'file_data': file_data,
        'file_type': file_type,
        'file_name': file_name,
        'preview': (message_text[:50] + '...') if message_text else (file_name or 'Запрос')
    }
    
    # Добавляем в очередь
    request_id, position_info = request_queue.add_request(
        user_id, session_id, request_data, user_class
    )
    
    # Возвращаем информацию о позиции в очереди
    return jsonify({
        'status': 'queued',
        'request_id': request_id,
        'position': position_info['position'],
        'estimated_wait': position_info['estimated_seconds'],
        'message': f'Запрос поставлен в очередь (позиция {position_info["position"]}, ожидание ~{position_info["estimated_seconds"]}с)'
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