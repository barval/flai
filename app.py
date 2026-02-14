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

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

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
LLM_CHAT_MODEL = os.getenv('LLM_CHAT_MODEL', 'qwen3-vl:8b-instruct-q4_K_M')
LLM_MULTIMODAL_MODEL = os.getenv('LLM_MULTIMODAL_MODEL', 'qwen3-vl:8b-instruct-q4_K_M')
LLM_REASONING_MODEL = os.getenv('LLM_REASONING_MODEL', 'gpt-oss-20b')

# Контекстные окна для разных моделей
MODEL_CONTEXT_WINDOWS = {
    LLM_CHAT_MODEL: int(os.getenv('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768)),
    LLM_MULTIMODAL_MODEL: int(os.getenv('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768)),
    LLM_REASONING_MODEL: int(os.getenv('LLM_REASONING_MODEL_CONTEXT_WINDOW', 65536)),
}

# -------------------------------
# Поддерживаемые форматы изображений
# -------------------------------
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

# Ограничения для изображений
MAX_IMAGE_SIZE_MB = 5
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024
MAX_IMAGE_DIMENSION = 3840  # 3840×2160

# -------------------------------
# Настройка путей к БД
# -------------------------------
DATA_DIR = 'data'
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

CHAT_DB_PATH = os.path.join(DATA_DIR, 'chat.db')

# -------------------------------
# Функция для получения текущего времени в заданном часовом поясе
# -------------------------------
def get_current_time_in_timezone():
    """
    Возвращает текущее время в часовом поясе, указанном в .env
    Формат: ДД.ММ.ГГГГ день_недели ЧЧ:ММ:СС
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
            tz_abbr = f" ({tz_abbr})"
        else:
            tz_abbr = ""
        
        return f"{formatted_date} {weekday_ru} {formatted_time}{tz_abbr}"
    
    except Exception as e:
        app.logger.error(f"Ошибка получения времени в часовом поясе {TIMEZONE_STR}: {str(e)}")
        # Fallback на UTC с днем недели на английском
        utc_time = datetime.now(pytz.UTC)
        weekdays_en = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return f"{utc_time.strftime('%d.%m.%Y')} {weekdays_en[utc_time.weekday()]} {utc_time.strftime('%H:%M:%S')} UTC"

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
# Функция для проверки изображения
# -------------------------------
def validate_image_file(file_data, file_type, file_name, file_size):
    """
    Проверяет, является ли файл поддерживаемым изображением и соответствует ли ограничениям
    Возвращает (is_valid, error_message)
    """
    # Проверка размера файла
    if file_size > MAX_IMAGE_SIZE_BYTES:
        return False, f"Максимальный размер файла с изображением {MAX_IMAGE_SIZE_MB}Мб"
    
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
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
            return False, f"Максимальное разрешение файла с изображением - не более {MAX_IMAGE_DIMENSION}×{MAX_IMAGE_DIMENSION}"
        
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

def call_ollama_chat(messages, model=None, stream=False):
    """Вызов Ollama API для чата"""
    if model is None:
        model = LLM_CHAT_MODEL
    
    try:
        payload = {
            'model': model,
            'messages': messages,
            'stream': stream,
            'options': {
                'num_ctx': MODEL_CONTEXT_WINDOWS.get(model, 32768),
                'temperature': 0.7,
                'top_p': 0.9,
            }
        }
        
        app.logger.info(f"Отправка запроса к Ollama. Модель: {model}")
        if any('images' in msg for msg in messages):
            app.logger.info("Запрос содержит изображение(я)")
        
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            app.logger.info("Успешный ответ от Ollama")
            return result['message']['content']
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
# Функция для создания промпта-маршрутизатора
# -------------------------------
def create_router_prompt(user_query, current_time_str):
    """
    Создает промпт для модели-маршрутизатора
    """
    prompt = f"""# РОЛЬ
Ты — маршрутизатор запросов и ассистент. Ты должен принять только одно из описанных решений на основе запроса.

# КРИТЕРИИ ОЦЕНКИ ЗАПРОСА

## 1. ПРОСТОЙ ЗАПРОС (Ответь сразу)
Запрос считается простым, если для ответа на него достаточно информации предоставленной во входных данных (например, текущее время).
- **Примеры:** "Который час?", "Какой сегодня день недели?".
- **Действие:** Дай краткий, точный ответ сразу на русском языке. Не добавляй рассуждений. 
- **Исключения:** Если требуется любые математические расчёты (сколько будет 5*25?) или расчёт временных интервалов (например, "Сколько дней до конца месяца?") - это СЛОЖНЫЙ ЗАПРОС.

## 2. ЗАПРОС НА СОЗДАНИЕ ИЗОБРАЖЕНИЯ (Просто выведи его)
Запрос на создание изображения, если в запросе присутствуют фразы связанные с просьбой создать изображение.
- **Примеры:** "Нарисуй лес", "Создай эскиз кошки", "Сделай фотографию девушки в шапке", "Подготовь рисунок слона"
- **Действие:** Выведи текст запроса на создание изображения без каких-либо изменений. Больше ничего не пиши.

## 3. ЗАПРОС НА ПРОСМОТР КОМНАТ (Замени и выведи)
Запрос на просмотр комнат, если в запросе присутствуют фразы связанные с просьбой показать одну из комнат (тамбур, прихожую, коридор, спальню, кабинет, детскую, гостиную, кухню, балкон).
- **Примеры:** 
  - Исходный: "Покажи кабинет" -> Замена и вывод: "/cam kab"
  - "Что в гостиной" -> Замена и вывод: "/cam gos"
  - "Есть ли кто-то в тамбуре" -> Замена и вывод: "/cam tam"
- **Действие:** В зависимости от того, какую комнату запрашивают показать - нужно выполнить замену запроса и вывести его. Больше ничего не пиши. 
- **Критерии для замены текста запроса:**
  - Запрос показать тамбур -> замена запроса на -> "/cam tam"
  - Запрос показать прихожую -> замена запроса на -> "/cam pri"
  - Запрос показать коридор -> замена запроса на -> "/cam kor"
  - Запрос показать спальню -> замена запроса на -> "/cam spa"
  - Запрос показать кабинет -> замена запроса на -> "/cam kab"
  - Запрос показать детскую -> замена запроса на -> "/cam det"
  - Запрос показать гостиную -> замена запроса на -> "/cam gos"
  - Запрос показать кухню -> замена запроса на -> "/cam kuh"
  - Запрос показать балкон -> замена запроса на -> "/cam bal"
  - Запрос показать что-то не указанное в списке -> замена запроса на -> "/cam "

## 4. СЛОЖНЫЙ ЗАПРОС (Просто выведи его)
Запрос сложный, если не подошёл не под одну перечисленную выше категорию.
- **Действие:** Выведи текст сложного запроса без каких-либо изменений. Не отвечай на сложный запрос сам.

# ВХОДНЫЕ ДАННЫЕ
- **Текущее время:** {current_time_str}
- **Запрос пользователя:** {user_query}

# ФОРМАТ ВЫВОДА
Выбери только один вариант из перечисленных ниже. Не пиши ничего, кроме указанного.

## Вариант 1 (Простой запрос):
Краткий ответ на простой запрос.
...

## Вариант 2 (Запрос на создание изображения):
/2: {user_query}
...

## Вариант 3 (Запрос на просмотр комнат):
/3: Заменённый запрос
...

## Вариант 4 (Сложный запрос):
/4: {user_query}"""
    
    return prompt

# -------------------------------
# Функция для обработки ответа маршрутизатора
# -------------------------------
def process_router_response(router_response, user_query):
    """
    Анализирует ответ от модели-маршрутизатора и возвращает:
    - action: тип действия ('simple', 'image', 'camera', 'complex')
    - processed_text: обработанный текст для дальнейшего использования
    """
    router_response = router_response.strip()
    
    # Проверяем на наличие меток в начале ответа
    if router_response.startswith('/2') or router_response.startswith('/2:'):
        # Запрос на создание изображения
        if router_response.startswith('/2:'):
            processed = router_response[3:].strip()
        else:
            processed = router_response[2:].strip()
        
        # Если после удаления метки текст пустой, используем исходный запрос
        if not processed:
            processed = user_query
            
        return 'image', f"[ЗАПРОС ИЗОБРАЖЕНИЯ] {processed}"
    
    elif router_response.startswith('/3') or router_response.startswith('/3:'):
        # Запрос на просмотр комнат - модель уже сделала замену
        if router_response.startswith('/3:'):
            processed = router_response[3:].strip()
        else:
            processed = router_response[2:].strip()
        
        # Модель уже должна была вернуть что-то вроде "/cam spa" или "/cam tam"
        # Если вдруг вернулось пустое, используем общий префикс
        if not processed:
            processed = "/cam "
            
        return 'camera', f"[ЗАПРОС КАМЕРЫ] {processed}"
    
    elif router_response.startswith('/4') or router_response.startswith('/4:'):
        # Сложный запрос - нужно передать в reasoning модель
        if router_response.startswith('/4:'):
            processed = router_response[3:].strip()
        else:
            processed = router_response[2:].strip()
        
        # Если после удаления метки текст пустой, используем исходный запрос
        if not processed:
            processed = user_query
            
        return 'complex', processed
    
    else:
        # Если нет меток, считаем это простым ответом
        return 'simple', router_response

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
    
    return jsonify(get_timezone_info())

# -------------------------------
# ОТПРАВКА СООБЩЕНИЯ (НОВАЯ ВЕРСИЯ С МАРШРУТИЗАЦИЕЙ)
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
            
            # ФОРМИРУЕМ ПРОМПТ ДЛЯ МОДЕЛИ С ИЗОБРАЖЕНИЕМ
            if message_text.strip():
                # Есть и текст, и изображение
                final_message_text = f"Текущее время: {current_time_str}. Подпись под изображением: {message_text}"
            else:
                # Только изображение, без текста
                final_message_text = f"Текущее время: {current_time_str}. Ответ - на русском языке. Списком перечисли все предметы на изображении. Опиши само изображение и всё, что можно про него рассказать. Не задавай вопросов. Не пиши о том, чего нет на изображении."
            
            # Отправляем запрос с изображением напрямую в мультимодальную модель
            ollama_messages = [{
                'role': 'user',
                'content': final_message_text,
                'images': [file_data]  # Изображение в base64
            }]
            
            app.logger.info(f"Отправка запроса с изображением к модели {LLM_MULTIMODAL_MODEL}")
            
            # ЗАМЕР ВРЕМЕНИ ВЫПОЛНЕНИЯ
            start_time = time.time()
            bot_reply = call_ollama_chat(ollama_messages, model=LLM_MULTIMODAL_MODEL)
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
        
        # ШАГ 1: Отправляем запрос в модель-маршрутизатор (LLM_CHAT_MODEL)
        router_prompt = create_router_prompt(message_text, current_time_str)
        router_messages = [{'role': 'user', 'content': router_prompt}]
        
        app.logger.info(f"Отправка запроса в модель-маршрутизатор: {LLM_CHAT_MODEL}")
        
        start_time = time.time()
        router_response = call_ollama_chat(router_messages, model=LLM_CHAT_MODEL)
        
        # ШАГ 2: Анализируем ответ маршрутизатора
        action, processed_text = process_router_response(router_response, message_text)
        
        app.logger.info(f"Результат маршрутизации: action={action}, processed_text={processed_text[:100]}...")
        
        # ШАГ 3: Обрабатываем в зависимости от типа действия
        final_response = ""
        model_used = LLM_CHAT_MODEL
        model_category = action
        
        if action == 'simple':
            # Простой ответ - используем ответ маршрутизатора напрямую
            final_response = processed_text
            model_used = LLM_CHAT_MODEL
            
        elif action == 'image':
            # Запрос на создание изображения - добавляем префикс
            final_response = processed_text
            model_used = LLM_CHAT_MODEL
            
        elif action == 'camera':
            # Запрос на просмотр комнат - добавляем префикс
            final_response = processed_text
            model_used = LLM_CHAT_MODEL
            
        elif action == 'complex':
            # Сложный запрос - отправляем в reasoning модель
            complex_messages = [{'role': 'user', 'content': processed_text}]
            
            app.logger.info(f"Отправка сложного запроса в модель {LLM_REASONING_MODEL}")
            
            # Отправляем в reasoning модель
            complex_response = call_ollama_chat(complex_messages, model=LLM_REASONING_MODEL)
            final_response = complex_response
            model_used = LLM_REASONING_MODEL
            
        else:
            # Неизвестный тип - используем ответ маршрутизатора как есть
            final_response = router_response
            model_used = LLM_CHAT_MODEL
        
        end_time = time.time()
        response_time = round(end_time - start_time, 1)
        
        # Сохраняем ответ
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