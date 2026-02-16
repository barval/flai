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
# Функции для работы с БД (остаются без изменений)
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
                    if email and password:
                        users[email] = {'password': password}
                    else:
                        app.logger.error(f"Пустые поля в строке {line_num}")
                else:
                    app.logger.error(f"Некорректная строка {line_num}")
        
        if not users:
            app.logger.error("В файле users.list нет валидных записей")
    else:
        app.logger.error("users.list not found")
    
    return users

USERS = load_users()

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
# API для работы с сеансами (остаются без изменений)
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
# ОТПРАВКА СООБЩЕНИЯ (обновленная версия с модулями)
# -------------------------------
@app.route('/send_message', methods=['POST'])
def send_message():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    # Проверяем базовый модуль
    if not modules['base'].available:
        return jsonify({'error': 'Базовый сервис чата недоступен'}), 500
    
    user_id = session['email']
    session_id = session.get('current_session')
    
    if not session_id:
        session_id = create_session(user_id)
        session['current_session'] = session_id
    
    # Получаем данные из запроса
    message_text = ""
    file_data = None
    file_type = None
    file_name = None
    file_size = 0
    
    if 'multipart/form-data' in request.content_type:
        message_text = request.form.get('message', '')
        
        if 'file' in request.files:
            file = request.files['file']
            if file.filename:
                file.seek(0, os.SEEK_END)
                file_size = file.tell()
                file.seek(0)
                
                file_data = base64.b64encode(file.read()).decode('utf-8')
                file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
                file_name = file.filename
    else:
        data = request.get_json()
        message_text = data.get('message', '')
    
    # Получаем текущее время
    current_time_str = get_current_time_in_timezone()
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
    
    # ОБРАБОТКА ФАЙЛА
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
        
        # Проверяем, является ли файл изображением
        if 'multimodal' in modules and modules['multimodal'].available:
            is_valid, error = modules['multimodal'].validate_image(file_data, file_type, file_name, file_size)
            
            if is_valid:
                # Обрабатываем изображение через мультимодальную модель
                start_time = time.time()
                bot_reply, error = modules['multimodal'].process_image_with_text(
                    file_data, message_text, current_time_str
                )
                
                if error:
                    bot_reply = f"⚠️ {error}"
                
                end_time = time.time()
                response_time = round(end_time - start_time, 1)
                
                save_message(session_id, 'assistant', bot_reply, model_name=app.config['LLM_MULTIMODAL_MODEL'])
                
                return jsonify({
                    'response': bot_reply,
                    'session_id': session_id,
                    'model_used': app.config['LLM_MULTIMODAL_MODEL'],
                    'model_category': 'multimodal',
                    'response_time': response_time,
                    'assistant_timestamp': current_time_for_db
                })
            else:
                # Неподдерживаемый файл
                bot_reply = f"⚠️ {error}"
                save_message(session_id, 'assistant', bot_reply, model_name='system')
                
                return jsonify({
                    'response': bot_reply,
                    'session_id': session_id,
                    'model_used': 'system',
                    'response_time': 0,
                    'assistant_timestamp': current_time_for_db
                })
        else:
            # Мультимодальный модуль недоступен
            bot_reply = "⚠️ Мультимодальная модель недоступна. Файлы не поддерживаются."
            save_message(session_id, 'assistant', bot_reply, model_name='system')
            
            return jsonify({
                'response': bot_reply,
                'session_id': session_id,
                'model_used': 'system',
                'response_time': 0,
                'assistant_timestamp': current_time_for_db
            })
    
    # ОБРАБОТКА ТЕКСТОВОГО СООБЩЕНИЯ
    if not message_text.strip():
        return jsonify({'error': 'Пустое сообщение'}), 400
    
    save_message(session_id, 'user', json.dumps(user_content, ensure_ascii=False), 
                None, None, None, None)
    
    if is_first_message:
        update_session_title(session_id, message_text, None)
    
    # Обрабатываем через базовый модуль
    start_time = time.time()
    router_result = modules['base'].process_message(message_text, current_time_str)
    
    if 'error' in router_result:
        return jsonify({'error': router_result['error']}), 500
    
    action_type = router_result['action']
    query = router_result['query']
    
    final_response = ""
    model_used = app.config['LLM_CHAT_MODEL']
    model_category = action_type
    
    # Обработка в зависимости от типа действия
    if action_type == 'image':
        # Запрос на создание изображения
        if 'image' in modules and modules['image'].available and 'multimodal' in modules:
            app.logger.info("Обработка запроса на создание изображения")
            
            image_result = modules['image'].generate_image(query, start_time)
            
            if image_result['success']:
                message_text = f"Изображение сгенерировано моделью {app.config['AUTOMATIC1111_MODEL']} по запросу: {query}"
                
                save_message(
                    session_id, 'assistant', message_text,
                    image_result['image_data'], image_result['file_type'],
                    image_result['file_name'], app.config['LLM_MULTIMODAL_MODEL']
                )
                
                return jsonify({
                    'response': message_text,
                    'session_id': session_id,
                    'model_used': app.config['LLM_MULTIMODAL_MODEL'],
                    'model_category': model_category,
                    'response_time': round(time.time() - start_time, 1),
                    'assistant_timestamp': current_time_for_db,
                    'generated_image': image_result['image_data'],
                    'file_name': image_result['file_name'],
                    'file_size': image_result['file_size'],
                    'file_type': image_result['file_type']
                })
            else:
                final_response = f"⚠️ {image_result['error']}"
        else:
            final_response = "⚠️ Модуль генерации изображений недоступен"
    
    elif action_type == 'camera':
        # Запрос к камере
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
                
                return jsonify({
                    'response': f"Изображение с камеры: {camera_result['room_name']}",
                    'session_id': session_id,
                    'model_used': model_used,
                    'model_category': model_category,
                    'response_time': round(time.time() - start_time, 1),
                    'assistant_timestamp': current_time_for_db,
                    'generated_image': camera_result['image_data'],
                    'file_name': camera_result['file_name'],
                    'file_size': camera_result['file_size'],
                    'file_type': camera_result['image_type']
                })
            else:
                final_response = f"⚠️ {camera_result['error']}"
        else:
            final_response = "⚠️ Модуль видеонаблюдения недоступен"
    
    elif action_type == 'reasoning':
        # Сложный запрос
        if router_result.get('needs_reasoning'):
            app.logger.info("Обработка сложного запроса через reasoning модель")
            final_response = modules['base'].process_reasoning(query, current_time_str)
            model_used = app.config['LLM_REASONING_MODEL']
        else:
            final_response = query
    
    else:  # action_type == 'none'
        final_response = query
    
    end_time = time.time()
    response_time = round(end_time - start_time, 1)
    
    # Сохраняем ответ
    if final_response:
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