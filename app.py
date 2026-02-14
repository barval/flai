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
from PIL import Image
from io import BytesIO
import pytz
from pytz.exceptions import UnknownTimeZoneError
import logging
import re

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# Настройка более подробного логирования
logging.basicConfig(level=logging.DEBUG)

# -------------------------------
# Подпись в футере - единая для всего проекта
# -------------------------------
FOOTER_TEXT = os.getenv('FOOTER_TEXT', 'ИИ Локальный v2.0 (с) 2026 Барсуков Валерий')

# -------------------------------
# Настройки часового пояса из .env
# -------------------------------
TIMEZONE_STR = os.getenv('TIMEZONE', 'Europe/Moscow')  # По умолчанию Москва

# Проверяем валидность часового пояса
try:
    TIMEZONE = pytz.timezone(TIMEZONE_STR)
    app.logger.info(f"Используется часовой пояс: {TIMEZONE_STR}")
except UnknownTimeZoneError:
    app.logger.warning(f"Неизвестный часовой пояс '{TIMEZONE_STR}'. Используется UTC.")
    TIMEZONE = pytz.UTC
    TIMEZONE_STR = 'UTC'

# -------------------------------
# Настройки Ollama
# -------------------------------
OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://localhost:11434')
LLM_CHAT_MODEL = os.getenv('LLM_CHAT_MODEL', 'qwen3:4b-instruct-2507-q4_K_M')
LLM_MULTIMODAL_MODEL = os.getenv('LLM_MULTIMODAL_MODEL', 'qwen3-vl:8b-instruct-q4_K_M')
LLM_REASONING_MODEL = os.getenv('LLM_REASONING_MODEL', 'qwen3:14b-q4_K_M')

# -------------------------------
# Настройки температуры для разных моделей
# -------------------------------
LLM_CHAT_TEMPERATURE = float(os.getenv('LLM_CHAT_TEMPERATURE', 0.1))
LLM_CHAT_TOP_P = float(os.getenv('LLM_CHAT_TOP_P', 0.1))

LLM_MULTIMODAL_TEMPERATURE = float(os.getenv('LLM_MULTIMODAL_TEMPERATURE', 0.7))
LLM_MULTIMODAL_TOP_P = float(os.getenv('LLM_MULTIMODAL_TOP_P', 0.9))

LLM_REASONING_TEMPERATURE = float(os.getenv('LLM_REASONING_TEMPERATURE', 0.7))
LLM_REASONING_TOP_P = float(os.getenv('LLM_REASONING_TOP_P', 0.9))

# Контекстные окна для разных моделей
MODEL_CONTEXT_WINDOWS = {
    LLM_CHAT_MODEL: int(os.getenv('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768)),
    LLM_MULTIMODAL_MODEL: int(os.getenv('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768)),
    LLM_REASONING_MODEL: int(os.getenv('LLM_REASONING_MODEL_CONTEXT_WINDOW', 40960)),
}

# -------------------------------
# Настройки Automatic1111
# -------------------------------
AUTOMATIC1111_URL = os.getenv('AUTOMATIC1111_URL', 'http://host.docker.internal:7860')

# -------------------------------
# Настройки для изображений
# -------------------------------
MAX_IMAGE_WIDTH = int(os.getenv('MAX_IMAGE_WIDTH', 3840))
MAX_IMAGE_HEIGHT = int(os.getenv('MAX_IMAGE_HEIGHT', 2160))
MAX_IMAGE_SIZE_MB = int(os.getenv('MAX_IMAGE_SIZE_MB', 5))
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024

# Поддерживаемые форматы изображений
SUPPORTED_IMAGE_EXTENSIONS = {
    '.jpg', '.jpeg', '.jpe',  # JPEG
    '.png',                     # PNG
    '.bmp',                     # BMP
    '.webp',                    # WebP
    '.tif', '.tiff'             # TIFF
}

SUPPORTED_IMAGE_MIMETYPES = {
    'image/jpeg', 'image/jpg', 'image/jpe',
    'image/png',
    'image/bmp', 'image/x-ms-bmp',
    'image/webp',
    'image/tiff', 'image/tif'
}

# -------------------------------
# Настройка путей к БД и шаблонам
# -------------------------------
DATA_DIR = 'data'
PROMPTS_DIR = 'prompts'

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

CHAT_DB_PATH = os.path.join(DATA_DIR, 'chat.db')

# -------------------------------
# Функция для получения текущего времени в заданном часовом поясе
# -------------------------------
def get_current_time_in_timezone():
    """
    Возвращает текущее время в часовом поясе, указанном в .env
    Формат: ДД.ММ.ГГГГ день_недели ЧЧ:ММ:СС (часовой_пояс)
    """
    try:
        # Получаем текущее время в UTC
        utc_now = datetime.now(pytz.UTC)
        
        # Конвертируем в нужный часовой пояс
        local_time = utc_now.astimezone(TIMEZONE)
        
        # Дни недели на русском
        weekdays_ru = {
            0: 'понедельник',
            1: 'вторник', 
            2: 'среда',
            3: 'четверг',
            4: 'пятница',
            5: 'суббота',
            6: 'воскресенье'
        }
        
        # Форматируем дату и время
        formatted_date = local_time.strftime('%d.%m.%Y')
        formatted_time = local_time.strftime('%H:%M:%S')
        weekday_ru = weekdays_ru[local_time.weekday()]
        
        # Добавляем часовой пояс для информации
        tz_abbr = local_time.strftime('%z')
        if tz_abbr:
            # Преобразуем +0300 в (+300)
            tz_abbr = f"(+{int(tz_abbr[1:3])})" if tz_abbr.startswith('+') else f"({tz_abbr})"
        else:
            tz_abbr = ""
        
        return f"{formatted_date} {formatted_time} {weekday_ru} {tz_abbr}"
    
    except Exception as e:
        app.logger.error(f"Ошибка получения времени в часовом поясе {TIMEZONE_STR}: {str(e)}")
        # Fallback на UTC с днем недели на английском
        utc_time = datetime.now(pytz.UTC)
        weekdays_en = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return f"{utc_time.strftime('%d.%m.%Y')} {utc_time.strftime('%H:%M:%S')} {weekdays_en[utc_time.weekday()]} (UTC)"

def get_current_time_in_timezone_for_db():
    """
    Возвращает текущее время в часовом поясе, указанном в .env
    в формате, подходящем для SQLite (YYYY-MM-DD HH:MM:SS)
    """
    try:
        # Получаем текущее время в UTC
        utc_now = datetime.now(pytz.UTC)
        
        # Конвертируем в нужный часовой пояс
        local_time = utc_now.astimezone(TIMEZONE)
        
        # Возвращаем в формате SQLite (без временной зоны)
        return local_time.strftime('%Y-%m-%d %H:%M:%S')
    
    except Exception as e:
        app.logger.error(f"Ошибка получения времени в часовом поясе {TIMEZONE_STR}: {str(e)}")
        # Fallback на локальное время сервера
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

# -------------------------------
# Функция для загрузки шаблона промпта
# -------------------------------
def load_prompt_template(template_name):
    """
    Загружает шаблон промпта из файла
    """
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
    """
    Загружает шаблон и подставляет переменные
    """
    template = load_prompt_template(template_name)
    if not template:
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
# Функция для вызова Automatic1111
# -------------------------------
def call_automatic1111(prompt_data):
    """
    Отправляет запрос в Automatic1111 и возвращает сгенерированное изображение
    """
    try:
        # Формируем payload для API Automatic1111
        payload = {
            "prompt": prompt_data.get("prompt", ""),
            "negative_prompt": prompt_data.get("negative_prompt", ""),
            "steps": int(prompt_data.get("steps", 40)),
            "width": int(prompt_data.get("width", 512)),
            "height": int(prompt_data.get("height", 512)),
            "cfg_scale": float(prompt_data.get("cfg_scale", 7)),
            "sampler_name": prompt_data.get("sampler_name", "DPM++ 2M Karras"),
            "batch_size": int(prompt_data.get("batch_size", 1)),
            "enable_hr": prompt_data.get("enable_hr") == "true",
            "hr_scale": float(prompt_data.get("hr_scale", 2)),
            "hr_upscaler": prompt_data.get("hr_upscaler", "Latent (nearest)"),
            "denoising_strength": float(prompt_data.get("denoising_strength", 0.7)),
            "hr_second_pass_steps": int(prompt_data.get("hr_second_pass_steps", 25))
        }
        
        app.logger.info(f"Отправка запроса в Automatic1111: {AUTOMATIC1111_URL}/sdapi/v1/txt2img")
        app.logger.debug(f"Payload: {json.dumps(payload, ensure_ascii=False)[:200]}...")
        
        response = requests.post(
            f"{AUTOMATIC1111_URL}/sdapi/v1/txt2img",
            json=payload,
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('images') and len(result['images']) > 0:
                # Automatic1111 возвращает изображения в формате base64 без префикса
                image_data = result['images'][0]
                app.logger.info(f"Получено изображение от Automatic1111, размер base64: {len(image_data)}")
                return {
                    'success': True,
                    'image_data': image_data  # Это уже готовая base64 строка
                }
            else:
                app.logger.error("Automatic1111 не вернул изображение")
                return {
                    'success': False,
                    'error': 'Automatic1111 не вернул изображение'
                }
        else:
            app.logger.error(f"Automatic1111 error: {response.status_code} - {response.text}")
            return {
                'success': False,
                'error': f"Ошибка Automatic1111: {response.status_code}"
            }
            
    except requests.exceptions.ConnectionError:
        app.logger.error("Ошибка подключения к Automatic1111")
        return {
            'success': False,
            'error': "Не удалось подключиться к Automatic1111. Проверьте, запущен ли сервис."
        }
    except requests.exceptions.Timeout:
        app.logger.error("Таймаут при обращении к Automatic1111")
        return {
            'success': False,
            'error': "Превышено время ожидания ответа от Automatic1111"
        }
    except Exception as e:
        app.logger.error(f"Error calling Automatic1111: {str(e)}")
        return {
            'success': False,
            'error': f"Ошибка при обращении к Automatic1111: {str(e)}"
        }

# -------------------------------
# Функция для вызова API видеонаблюдения
# -------------------------------
def call_camera_api(cam_query):
    """
    Запрашивает изображение с камеры
    """
    try:
        camera_url = f"http://host.docker.internal:5005/snapshot/{cam_query}"
        app.logger.info(f"Запрос к камере: {camera_url}")
        
        response = requests.get(camera_url, timeout=10)
        
        if response.status_code == 200:
            # Получаем изображение и конвертируем в base64
            image_data = base64.b64encode(response.content).decode('utf-8')
            content_type = response.headers.get('content-type', 'image/jpeg')
            
            return {
                'success': True,
                'image_data': image_data,
                'image_type': content_type
            }
        else:
            app.logger.error(f"Camera API error: {response.status_code} - {response.text}")
            return {
                'success': False,
                'error': f"Ошибка камеры: {response.status_code}"
            }
            
    except requests.exceptions.ConnectionError:
        app.logger.error("Ошибка подключения к API камер")
        return {
            'success': False,
            'error': "Не удалось подключиться к сервису видеонаблюдения"
        }
    except Exception as e:
        app.logger.error(f"Error calling camera API: {str(e)}")
        return {
            'success': False,
            'error': f"Ошибка при обращении к камере: {str(e)}"
        }

# -------------------------------
# Функция для проверки изображения
# -------------------------------
def validate_image_file(file_data, file_type, file_name, file_size):
    """
    Проверяет, является ли файл поддерживаемым изображением и соответствует ли ограничениям
    Возвращает (is_valid, error_message)
    """
    # Проверка размера файла
    if file_size > MAX_IMAGE_SIZE_BYTES:
        return False, f"Максимальный размер файла с изображением {MAX_IMAGE_SIZE_MB} Мб"
    
    # Проверка MIME-типа
    if file_type not in SUPPORTED_IMAGE_MIMETYPES:
        # Проверяем по расширению, если MIME-тип неопределенный
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in SUPPORTED_IMAGE_EXTENSIONS:
            return False, "Файлы данного типа пока не поддерживаются."
    
    # Проверка размеров изображения
    try:
        # Декодируем base64 в байты
        image_bytes = base64.b64decode(file_data)
        
        # Открываем изображение через PIL
        img = Image.open(BytesIO(image_bytes))
        width, height = img.size
        
        # Проверяем максимальное разрешение
        if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
            return False, f"Максимальное разрешение файла с изображением - не более {MAX_IMAGE_WIDTH}×{MAX_IMAGE_HEIGHT}"
        
        return True, None
        
    except Exception as e:
        app.logger.error(f"Ошибка при проверке изображения: {str(e)}")
        return False, "Не удалось обработать файл изображения"

# -------------------------------
# Функции для работы с Ollama
# -------------------------------
def check_ollama_connection():
    """Проверка подключения к Ollama"""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get('models', [])
            app.logger.info(f"Ollama connected. Available models: {[m['name'] for m in models]}")
            return True, models
        else:
            return False, []
    except Exception as e:
        app.logger.error(f"Ollama connection failed: {str(e)}")
        return False, []

def call_ollama_chat(messages, model=None, stream=False, temperature=None, top_p=None):
    """Вызов Ollama API для чата"""
    if model is None:
        model = LLM_CHAT_MODEL
    
    # Выбираем параметры в зависимости от модели
    if temperature is None:
        if model == LLM_CHAT_MODEL:
            temperature = LLM_CHAT_TEMPERATURE
            top_p = LLM_CHAT_TOP_P if top_p is None else top_p
        elif model == LLM_MULTIMODAL_MODEL:
            temperature = LLM_MULTIMODAL_TEMPERATURE
            top_p = LLM_MULTIMODAL_TOP_P if top_p is None else top_p
        elif model == LLM_REASONING_MODEL:
            temperature = LLM_REASONING_TEMPERATURE
            top_p = LLM_REASONING_TOP_P if top_p is None else top_p
        else:
            temperature = 0.7
            top_p = 0.9
    
    try:
        payload = {
            'model': model,
            'messages': messages,
            'stream': stream,
            'options': {
                'num_ctx': MODEL_CONTEXT_WINDOWS.get(model, 32768),
                'temperature': temperature,
                'top_p': top_p,
                'stop': ['<|im_end|>', '<|endoftext|>', '\n\n\n'],  # Стоп-токены
            }
        }
        
        app.logger.info(f"Отправка запроса к Ollama. Модель: {model}, temp={temperature}, top_p={top_p}")
        
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result['message']['content']
            
            # Очищаем ответ от стоп-токенов
            for stop_token in ['<|endoftext|>', '<|im_end|>']:
                if stop_token in content:
                    content = content[:content.index(stop_token)]
            
            # Берём только первую строку для модели-маршрутизатора
            if model == LLM_CHAT_MODEL and temperature < 0.3:
                content = content.split('\n')[0].strip()
            
            return content.strip()
        else:
            error_msg = f"Ollama error: {response.status_code} - {response.text}"
            app.logger.error(error_msg)
            return f"⚠️ Ошибка Ollama: {response.status_code}"
            
    except requests.exceptions.ConnectionError:
        app.logger.error("Ошибка подключения к Ollama")
        return "⚠️ Не удалось подключиться к Ollama. Проверьте, запущен ли сервис."
    except requests.exceptions.Timeout:
        app.logger.error("Таймаут при обращении к Ollama")
        return "⚠️ Превышено время ожидания ответа от Ollama. Попробуйте ещё раз."
    except Exception as e:
        app.logger.error(f"Error calling Ollama: {str(e)}")
        return f"⚠️ Ошибка при обращении к Ollama: {str(e)}"

# -------------------------------
# Инициализация и миграция БД
# -------------------------------
def init_db():
    """Инициализация базы данных и миграция схемы"""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            
            # Сессии (профили пользователей)
            c.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    user_id TEXT PRIMARY KEY,
                    last_session_id TEXT
                )
            ''')
            
            # Сеансы чатов
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
            
            # Сообщения
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

# Инициализируем БД при старте
init_db()

# Проверяем подключение к Ollama при старте
ollama_available, ollama_models = check_ollama_connection()
if not ollama_available:
    app.logger.warning("Ollama is not available. Please check if Ollama is running.")

# -------------------------------
# Вспомогательные функции
# -------------------------------
def load_users():
    users = {}
    users_file = 'users.list'
    if os.path.exists(users_file):
        with open(users_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    parts = line.strip().split(',')
                    email = parts[0]
                    password = parts[1]
                    users[email] = {'password': password}
    else:
        app.logger.warning("users.list not found, creating default user")
        users['admin@local.com'] = {'password': 'admin123'}
    return users

USERS = load_users()

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
            # Преобразуем timestamp в ISO формат с часовым поясом для JS
            if msg_dict.get('timestamp'):
                try:
                    # Парсим timestamp из БД (формат: YYYY-MM-DD HH:MM:SS)
                    dt = datetime.strptime(msg_dict['timestamp'], '%Y-%m-%d %H:%M:%S')
                    # Добавляем информацию о часовом поясе
                    dt = TIMEZONE.localize(dt)
                    # Конвертируем в ISO формат для JS
                    msg_dict['timestamp'] = dt.isoformat()
                except Exception as e:
                    app.logger.error(f"Ошибка преобразования timestamp: {str(e)}")
            messages.append(msg_dict)
        
        return messages

def create_session(user_id, title="Новый сеанс"):
    """Создание нового сеанса без привязки к конкретной модели"""
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

def update_session_title(session_id, first_message):
    """Обновить заголовок сеанса на основе первого сообщения"""
    title = first_message[:40] + ('...' if len(first_message) > 40 else '')
    current_time = get_current_time_in_timezone_for_db()
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE chat_sessions
            SET title = ?, updated_at = ?
            WHERE id = ?
        ''', (title, current_time, session_id))
        conn.commit()

def save_message(session_id, role, content, file_data=None, file_type=None, file_name=None, model_name=None):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        
        # Получаем текущее время в нужном часовом поясе
        current_time = get_current_time_in_timezone_for_db()
        
        # Вставляем сообщение
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
        
        if email in USERS and USERS[email]['password'] == password:
            session['email'] = email
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
                         footer_text=FOOTER_TEXT)

# -------------------------------
# API для работы с сеансами
# -------------------------------
@app.route('/api/sessions', methods=['GET'])
def api_get_sessions():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user_id = session['email']
    sessions = get_user_sessions(user_id)
    return jsonify(sessions)

@app.route('/api/sessions/<session_id>/messages', methods=['GET'])
def api_get_messages(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    messages = get_session_messages(session_id)
    return jsonify(messages)

@app.route('/api/sessions/<session_id>/switch', methods=['POST'])
def api_switch_session(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    session['current_session'] = session_id
    set_last_session(session['email'], session_id)
    return jsonify({'status': 'ok'})

@app.route('/api/sessions/<session_id>/model-info', methods=['GET'])
def api_get_session_model(session_id):
    """Получить информацию о модели, используемой в сеансе"""
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        try:
            c.execute('SELECT model_name FROM chat_sessions WHERE id = ?', (session_id,))
            row = c.fetchone()
            
            if row:
                return jsonify({'model_name': row[0]})
            else:
                return jsonify({'model_name': 'auto'})
        except sqlite3.OperationalError:
            # Если колонка все еще не существует, возвращаем 'auto'
            return jsonify({'model_name': 'auto'})

@app.route('/api/sessions/new', methods=['POST'])
def api_new_session():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    session_id = create_session(user_id)
    session['current_session'] = session_id
    set_last_session(user_id, session_id)
    return jsonify({'id': session_id, 'title': 'Новый сеанс'})

# -------------------------------
# API для Ollama
# -------------------------------
@app.route('/api/ollama/status', methods=['GET'])
def api_ollama_status():
    """Проверка статуса Ollama"""
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    available, models = check_ollama_connection()
    return jsonify({
        'available': available,
        'models': [m['name'] for m in models] if available else []
    })

# -------------------------------
# API для получения подписи футера
# -------------------------------
@app.route('/api/footer-text', methods=['GET'])
def api_footer_text():
    """Возвращает текст подписи для футера"""
    return FOOTER_TEXT

# -------------------------------
# Отладочный эндпоинт для проверки часового пояса
# -------------------------------
@app.route('/api/timezone-info', methods=['GET'])
def api_timezone_info():
    """Возвращает информацию о текущем часовом поясе (только для разработки)"""
    if 'email' not in session and app.debug == False:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    return jsonify({
        'timezone': TIMEZONE_STR,
        'current_time': get_current_time_in_timezone(),
        'current_time_for_db': get_current_time_in_timezone_for_db()
    })

# -------------------------------
# Функция для обработки ответа с маркерами
# -------------------------------
def process_marker_response(response_text, current_time_str):
    """
    Анализирует ответ на наличие маркеров [-IMAGE-], [-CAMERA-], [-REASONING-]
    """
    response_text = response_text.strip()
    app.logger.debug(f"process_marker_response получил текст длиной {len(response_text)} символов")
    
    # Словарь маркеров
    markers = {
        '[-IMAGE-]': 'image',
        '[-CAMERA-]': 'camera',
        '[-REASONING-]': 'reasoning'
    }
    
    # Ищем маркеры
    for marker, action in markers.items():
        if marker in response_text:
            app.logger.info(f"Найден маркер {marker} в тексте")
            
            # Находим текст после маркера
            parts = response_text.split(marker, 1)
            if len(parts) > 1:
                processed = parts[1].strip()
                # Если после маркера ничего нет, возвращаем пустую строку
                if not processed:
                    processed = ""
                app.logger.info(f"Обработанный текст после маркера: '{processed[:100]}'")
                return action, processed
    
    # Если маркеров нет, возвращаем весь текст
    app.logger.info("Маркеры не обнаружены, возвращаем весь текст")
    return 'none', response_text

# -------------------------------
# ОТПРАВКА СООБЩЕНИЯ
# -------------------------------
@app.route('/send_message', methods=['POST'])
def send_message():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    session_id = session.get('current_session')
    
    if not session_id:
        session_id = create_session(user_id)
        session['current_session'] = session_id
    
    message_text = ""
    file_data = None
    file_type = None
    file_name = None
    file_size = 0
    
    if 'multipart/form-data' in request.content_type:
        message_text = request.form.get('message', '')
        app.logger.info(f"Получено multipart сообщение: '{message_text}'")
        
        if 'file' in request.files:
            file = request.files['file']
            if file.filename:
                file.seek(0, os.SEEK_END)
                file_size = file.tell()
                file.seek(0)
                
                file_data = base64.b64encode(file.read()).decode('utf-8')
                file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
                file_name = file.filename
                app.logger.info(f"Получен файл: {file_name}, тип: {file_type}, размер: {file_size}")
    else:
        data = request.get_json()
        message_text = data.get('message', '')
        app.logger.info(f"Получено JSON сообщение: '{message_text}'")
    
    # ПОЛУЧАЕМ ТЕКУЩЕЕ ВРЕМЯ В УКАЗАННОМ ЧАСОВОМ ПОЯСЕ
    current_time_str = get_current_time_in_timezone()
    current_time_for_db = get_current_time_in_timezone_for_db()
    
    # Логируем используемый часовой пояс для отладки
    app.logger.info(f"Используется часовой пояс: {TIMEZONE_STR}, время: {current_time_str}")
    
    # Проверяем, является ли это первым сообщением в сеансе
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
        msg_count = c.fetchone()[0]
        is_first_message = (msg_count == 0)
    
    # Сохраняем исходное сообщение пользователя (для отображения в интерфейсе)
    user_content = []
    if message_text:
        user_content.append({"type": "text", "text": message_text})
    
    # Проверяем, есть ли файл
    if file_data:
        # Проверяем, поддерживается ли файл как изображение
        is_valid_image, validation_error = validate_image_file(file_data, file_type, file_name, file_size)
        
        if is_valid_image:
            # Добавляем информацию о файле в user_content
            user_content.append({
                "type": "file", 
                "file_data": file_data, 
                "file_type": file_type, 
                "file_name": file_name
            })
            
            # Сохраняем сообщение пользователя с изображением
            save_message(session_id, 'user', json.dumps(user_content, ensure_ascii=False) if user_content else message_text,
                        file_data, file_type, file_name, None)
            
            # Обновляем заголовок для первого сообщения
            if is_first_message and message_text:
                update_session_title(session_id, message_text)
            
            # ВЫБОР ШАБЛОНА В ЗАВИСИМОСТИ ОТ НАЛИЧИЯ ТЕКСТА
            if message_text.strip():
                # Есть и текст, и изображение - используем image_text.template
                prompt = format_prompt('image_text.template', {
                    'current_time_str': current_time_str,
                    'user_query': message_text
                })
                app.logger.info("Используется шаблон image_text.template")
            else:
                # Только изображение, без текста - используем image.template
                prompt = format_prompt('image.template', {
                    'current_time_str': current_time_str
                })
                app.logger.info("Используется шаблон image.template")
            
            if not prompt:
                # Если шаблон не загрузился, используем запасной вариант
                if message_text.strip():
                    prompt = f"Текущее время: {current_time_str}. Подпись под изображением: {message_text}"
                else:
                    prompt = f"Текущее время: {current_time_str}. Списком перечисли все предметы на изображении. Опиши само изображение и всё, что можно про него рассказать."
            
            # Отправляем запрос с изображением напрямую в мультимодальную модель
            ollama_messages = [{
                'role': 'user',
                'content': prompt,
                'images': [file_data]  # Изображение в base64
            }]
            
            app.logger.info(f"Отправка запроса с изображением к модели {LLM_MULTIMODAL_MODEL}")
            
            # ЗАМЕР ВРЕМЕНИ ВЫПОЛНЕНИЯ
            start_time = time.time()
            bot_reply = call_ollama_chat(
                ollama_messages, 
                model=LLM_MULTIMODAL_MODEL,
                temperature=LLM_MULTIMODAL_TEMPERATURE,
                top_p=LLM_MULTIMODAL_TOP_P
            )
            end_time = time.time()
            response_time = round(end_time - start_time, 1)
            
            # Сохраняем ответ
            save_message(session_id, 'assistant', bot_reply, model_name=LLM_MULTIMODAL_MODEL)
            
            return jsonify({
                'response': bot_reply,
                'session_id': session_id,
                'model_used': LLM_MULTIMODAL_MODEL,
                'model_category': 'multimodal',
                'response_time': response_time,
                'assistant_timestamp': current_time_for_db
            })
        else:
            # Неподдерживаемый тип файла или превышены ограничения
            bot_reply = f"⚠️ {validation_error}"
            
            # Добавляем информацию о файле в user_content для истории
            user_content.append({
                "type": "file", 
                "file_data": file_data, 
                "file_type": file_type, 
                "file_name": file_name
            })
            
            # Сохраняем сообщение пользователя (для истории)
            save_message(session_id, 'user', json.dumps(user_content, ensure_ascii=False) if user_content else message_text,
                        file_data, file_type, file_name, None)
            
            # Обновляем заголовок для первого сообщения
            if is_first_message and message_text:
                update_session_title(session_id, message_text)
            
            # Сохраняем ответ-уведомление
            save_message(session_id, 'assistant', bot_reply, model_name='system')
            
            return jsonify({
                'response': bot_reply,
                'session_id': session_id,
                'model_used': 'system',
                'response_time': 0,
                'assistant_timestamp': current_time_for_db
            })
    else:
        # ТОЛЬКО ТЕКСТ, БЕЗ ФАЙЛОВ
        if not message_text.strip():
            return jsonify({'error': 'Пустое сообщение'}), 400
        
        # Сохраняем сообщение пользователя (только текст)
        save_message(session_id, 'user', json.dumps(user_content, ensure_ascii=False) if user_content else message_text,
                    None, None, None, None)
        
        # Обновляем заголовок для первого сообщения
        if is_first_message and message_text:
            update_session_title(session_id, message_text)
        
        # ШАГ 1: Формируем промпт из base_text.template
        prompt = format_prompt('base_text.template', {
            'current_time_str': current_time_str,
            'user_query': message_text
        })
        
        if not prompt:
            # Упрощённый промпт, если шаблон не загрузился
            prompt = f"""Классифицируй запрос и ответь одной строкой.

Правила:
1. Если просят время/дату - ответь только числом или днём (например: "10:11" или "Суббота")
2. Если просят нарисовать/создать изображение - ответь: [-IMAGE-] текст запроса
3. Если просят показать комнату - ответь: [-CAMERA-] код комнаты (коды: тамбур=tam, прихожая=pri, коридор=kor, спальня=spa, кабинет=kab, детская=det, гостиная=gos, кухня=kuh, балкон=bal)
4. Для всего остального - ответь: [-REASONING-] текст запроса

Запрос: {message_text}
Время: {current_time_str}

Ответ (только одна строка, без пояснений):"""
        
        app.logger.info(f"Сформирован промпт для маршрутизатора: {prompt}")
        
        # Отправляем запрос в модель-маршрутизатор с системным промптом
        router_messages = [
            {
                'role': 'system',
                'content': 'Ты - маршрутизатор запросов. Отвечай ТОЛЬКО одной строкой на русском языке. Никаких пояснений.'
            },
            {'role': 'user', 'content': prompt}
        ]
        
        app.logger.info(f"Отправка запроса в модель-маршрутизатор: {LLM_CHAT_MODEL}")
        
        start_time = time.time()
        router_response = call_ollama_chat(
            router_messages, 
            model=LLM_CHAT_MODEL,
            temperature=LLM_CHAT_TEMPERATURE,
            top_p=LLM_CHAT_TOP_P
        )
        app.logger.info(f"Ответ от модели-маршрутизатора: '{router_response}'")
        
        # ШАГ 2: Анализируем ответ на наличие маркеров
        action_type, processed_text = process_marker_response(router_response, current_time_str)
        
        app.logger.info(f"Результат обработки маркеров: action_type={action_type}, processed_text='{processed_text[:100]}'...")
        
        # ШАГ 3: Обрабатываем в зависимости от типа действия
        final_response = ""
        model_used = LLM_CHAT_MODEL
        model_category = action_type
        
        if action_type == 'image':
            app.logger.info("Обработка запроса на создание изображения")
            
            # Формируем промпт из create_image.template
            create_prompt = format_prompt('create_image.template', {
                'image_query': processed_text
            })
            
            if not create_prompt:
                # Упрощённый промпт, если шаблон не загрузился
                create_prompt = f"""Analyze this request and return a JSON with parameters for image generation.
Request: {processed_text}

Return ONLY a valid JSON object with these fields:
- prompt: detailed description for image generation
- negative_prompt: what to avoid
- steps: 40
- width: 512
- height: 512
- cfg_scale: 7
- sampler_name: "DPM++ 2M Karras"
- batch_size: 1
- enable_hr: true
- hr_scale: 2
- hr_upscaler: "Latent (nearest)"
- denoising_strength: 0.7
- hr_second_pass_steps: 25

The response must be ONLY the JSON object, no other text."""
            
            app.logger.info(f"Отправка запроса в мультимодальную модель для генерации параметров")
            
            # Добавляем системный промпт для мультимодальной модели
            image_messages = [
                {
                    'role': 'system',
                    'content': 'You are an image generation parameter generator. Always respond with valid JSON only, no explanations.'
                },
                {'role': 'user', 'content': create_prompt}
            ]
            
            # Отправляем запрос в модель для генерации параметров изображения
            image_params_response = call_ollama_chat(
                image_messages, 
                model=LLM_MULTIMODAL_MODEL,
                temperature=LLM_MULTIMODAL_TEMPERATURE,
                top_p=LLM_MULTIMODAL_TOP_P
            )
            
            app.logger.info(f"Ответ от мультимодальной модели: {image_params_response[:200]}...")
            
            # Пытаемся распарсить JSON из ответа
            try:
                # Ищем JSON в ответе (между { и })
                json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', image_params_response, re.DOTALL)
                if json_match:
                    json_str = json_match.group()
                    app.logger.info(f"Найден JSON: {json_str[:200]}...")
                    prompt_data = json.loads(json_str)
                    
                    # Проверяем наличие обязательных полей
                    if 'prompt' not in prompt_data:
                        prompt_data['prompt'] = f"masterpiece, best quality, {processed_text}"
                    if 'negative_prompt' not in prompt_data:
                        prompt_data['negative_prompt'] = "worst quality, low quality"
                    
                    app.logger.info(f"Отправка запроса в Automatic1111")
                    
                    # Отправляем запрос в Automatic1111
                    image_result = call_automatic1111(prompt_data)
                    
                    if image_result['success']:
                        app.logger.info(f"Изображение успешно сгенерировано, размер данных: {len(image_result['image_data'])}")
                        
                        # Вычисляем размер файла в байтах из base64
                        # Каждые 4 символа base64 = 3 байта
                        file_size_bytes = int((len(image_result['image_data']) * 3) / 4)
                        
                        # Генерируем имя файла в формате ГГГГ-ММ-ДД_ЧЧ-ММ-СС.jpg
                        current_time_for_filename = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                        generated_filename = f"{current_time_for_filename}.jpg"
                        
                        # Формируем текст сообщения (без галочки)
                        message_text = f"Изображение сгенерировано по запросу: {processed_text}"
                        
                        # СОХРАНЯЕМ ИЗОБРАЖЕНИЕ В ЧАТ
                        save_message(
                            session_id, 
                            'assistant', 
                            message_text,
                            image_result['image_data'],  # Передаём base64 данные изображения
                            'image/jpeg',                 # MIME-тип для JPG
                            generated_filename,            # Имя файла в нужном формате
                            model_used
                        )
                        
                        # ВОЗВРАЩАЕМ ИЗОБРАЖЕНИЕ В ОТВЕТЕ
                        return jsonify({
                            'response': message_text,
                            'session_id': session_id,
                            'model_used': model_used,
                            'model_category': model_category,
                            'response_time': round(time.time() - start_time, 1),
                            'assistant_timestamp': current_time_for_db,
                            'generated_image': image_result['image_data'],
                            'file_name': generated_filename,
                            'file_size': file_size_bytes,
                            'file_type': 'image/jpeg'
                        })
                    else:
                        final_response = f"Ошибка: {image_result['error']}"
                else:
                    app.logger.warning(f"Не удалось найти JSON в ответе")
                    # Пробуем создать простой промпт вручную
                    simple_prompt = {
                        "prompt": f"masterpiece, best quality, ultra-detailed, {processed_text}",
                        "negative_prompt": "worst quality, low quality, bad anatomy",
                        "steps": "40",
                        "width": "512",
                        "height": "512",
                        "cfg_scale": "7",
                        "sampler_name": "DPM++ 2M Karras",
                        "batch_size": "1",
                        "enable_hr": "true",
                        "hr_scale": "2",
                        "hr_upscaler": "Latent (nearest)",
                        "denoising_strength": "0.7",
                        "hr_second_pass_steps": "25"
                    }
                    
                    app.logger.info("Используем автоматически сгенерированный промпт")
                    image_result = call_automatic1111(simple_prompt)
                    
                    if image_result['success']:
                        app.logger.info(f"Изображение успешно сгенерировано (авто-промпт), размер данных: {len(image_result['image_data'])}")
                        
                        # Вычисляем размер файла в байтах из base64
                        file_size_bytes = int((len(image_result['image_data']) * 3) / 4)
                        
                        # Генерируем имя файла в формате ГГГГ-ММ-ДД_ЧЧ-ММ-СС.jpg
                        current_time_for_filename = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
                        generated_filename = f"{current_time_for_filename}.jpg"
                        
                        # Формируем текст сообщения (без галочки)
                        message_text = f"Изображение сгенерировано по запросу: {processed_text}"
                        
                        # СОХРАНЯЕМ ИЗОБРАЖЕНИЕ В ЧАТ
                        save_message(
                            session_id, 
                            'assistant', 
                            message_text,
                            image_result['image_data'],
                            'image/jpeg',
                            generated_filename,
                            model_used
                        )
                        
                        # ВОЗВРАЩАЕМ ИЗОБРАЖЕНИЕ В ОТВЕТЕ
                        return jsonify({
                            'response': message_text,
                            'session_id': session_id,
                            'model_used': model_used,
                            'model_category': model_category,
                            'response_time': round(time.time() - start_time, 1),
                            'assistant_timestamp': current_time_for_db,
                            'generated_image': image_result['image_data'],
                            'file_name': generated_filename,
                            'file_size': file_size_bytes,
                            'file_type': 'image/jpeg'
                        })
                    else:
                        final_response = f"Ошибка: {image_result['error']}"
            except Exception as e:
                app.logger.error(f"Ошибка при обработке ответа для генерации изображения: {str(e)}")
                final_response = f"Ошибка при генерации изображения: {str(e)}"
            
        elif action_type == 'camera':
            app.logger.info("Обработка запроса к камере")
            # Запрос на просмотр комнат
            camera_result = call_camera_api(processed_text)
            
            if camera_result['success']:
                # Сохраняем сообщение с изображением от камеры
                save_message(
                    session_id, 
                    'assistant', 
                    f"Изображение с камеры: {processed_text}",
                    camera_result['image_data'],
                    camera_result.get('image_type', 'image/jpeg'),
                    f'camera_{processed_text}_{int(time.time())}.jpg',
                    model_used
                )
                
                return jsonify({
                    'response': f"📸 Изображение с камеры {processed_text}",
                    'session_id': session_id,
                    'model_used': model_used,
                    'model_category': model_category,
                    'response_time': round(time.time() - start_time, 1),
                    'assistant_timestamp': current_time_for_db,
                    'camera_image': camera_result['image_data']
                })
            else:
                final_response = f"Ошибка: {camera_result['error']}"
            
        elif action_type == 'reasoning':
            app.logger.info("Обработка сложного запроса через reasoning модель")
            # Сложный запрос для reasoning модели
            # Формируем промпт из reasoning.template
            reasoning_prompt = format_prompt('reasoning.template', {
                'current_time_str': current_time_str,
                'reasoning_query': processed_text
            })
            
            if not reasoning_prompt:
                reasoning_prompt = processed_text
            
            app.logger.info(f"Отправка запроса в reasoning модель")
            
            # Отправляем запрос в reasoning модель
            reasoning_response = call_ollama_chat(
                [{'role': 'user', 'content': reasoning_prompt}], 
                model=LLM_REASONING_MODEL,
                temperature=LLM_REASONING_TEMPERATURE,
                top_p=LLM_REASONING_TOP_P
            )
            
            final_response = reasoning_response
            model_used = LLM_REASONING_MODEL
            
        else:  # action_type == 'none'
            app.logger.info("Обычный текстовый ответ")
            # Обычный текстовый ответ
            final_response = processed_text
            model_used = LLM_CHAT_MODEL
        
        end_time = time.time()
        response_time = round(end_time - start_time, 1)
        
        # Сохраняем ответ
        if final_response:
            app.logger.info(f"Сохранение ответа: {final_response[:100]}...")
            save_message(session_id, 'assistant', final_response, model_name=model_used)
        
        return jsonify({
            'response': final_response,
            'session_id': session_id,
            'model_used': model_used,
            'model_category': model_category,
            'response_time': response_time,
            'assistant_timestamp': current_time_for_db
        })

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
        c.execute('UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?', ('Новый сеанс', current_time, session_id))
        conn.commit()
    
    return jsonify({'status': 'ok'})

# -------------------------------
# Удаление сеанса
# -------------------------------
@app.route('/api/sessions/<session_id>/delete', methods=['POST'])
def api_delete_session(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT user_id FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        
        if not row:
            return jsonify({'error': 'Сеанс не найден'}), 404
        
        if row[0] != user_id:
            return jsonify({'error': 'Нет прав на удаление этого сеанса'}), 403
        
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
# Статика и прочее
# -------------------------------
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.context_processor
def inject_footer():
    """Контекст-процессор для передачи подписи в шаблоны"""
    return {
        'footer_content': FOOTER_TEXT
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)