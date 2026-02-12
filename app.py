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
from pathlib import Path
import time

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# -------------------------------
# Настройки Ollama
# -------------------------------
OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://localhost:11434')
OLLAMA_CHAT_MODEL = os.getenv('LLM_CHAT_MODEL', 'qwen3-vl:8b-instruct-q4_K_M')
OLLAMA_MULTIMODAL_MODEL = os.getenv('LLM_MULTIMODAL_MODEL', 'qwen3-vl:8b-instruct-q4_K_M')
OLLAMA_REASONING_MODEL = os.getenv('LLM_REASONING_MODEL', 'gpt-oss-20b')

# Контекстные окна для разных моделей
MODEL_CONTEXT_WINDOWS = {
    OLLAMA_CHAT_MODEL: int(os.getenv('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768)),
    OLLAMA_MULTIMODAL_MODEL: int(os.getenv('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768)),
    OLLAMA_REASONING_MODEL: int(os.getenv('LLM_REASONING_MODEL_CONTEXT_WINDOW', 65536)),
}

# -------------------------------
# Настройка путей к БД
# -------------------------------
DATA_DIR = 'data'
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

CHAT_DB_PATH = os.path.join(DATA_DIR, 'chat.db')

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

def get_available_models():
    """Получить список доступных моделей из Ollama"""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if response.status_code == 200:
            return response.json().get('models', [])
    except Exception as e:
        app.logger.error(f"Failed to get models: {str(e)}")
    return []

def prepare_ollama_messages(messages):
    """Подготовка сообщений для Ollama API"""
    ollama_messages = []
    
    for msg in messages:
        role = msg['role']
        content = msg['content']
        
        if isinstance(content, str):
            if content.startswith('['):
                try:
                    parts = json.loads(content)
                    text_parts = []
                    for part in parts:
                        if part.get('type') == 'text':
                            text_parts.append(part['text'])
                        elif part.get('type') == 'file':
                            file_type = part.get('file_type', '')
                            file_data = part.get('file_data', '')
                            
                            if file_type.startswith('image/'):
                                ollama_messages.append({
                                    'role': role,
                                    'content': text_parts[-1] if text_parts else '',
                                    'images': [file_data]
                                })
                            elif file_type.startswith('audio/'):
                                text_parts.append(f"[Аудиофайл: {part.get('file_name', 'audio')}]")
                            else:
                                text_parts.append(f"[Документ: {part.get('file_name', 'file')}]")
                    
                    if text_parts and not any(m.get('role') == role and m.get('images') for m in ollama_messages):
                        ollama_messages.append({
                            'role': role,
                            'content': '\n'.join(text_parts)
                        })
                except:
                    ollama_messages.append({'role': role, 'content': content})
            else:
                ollama_messages.append({'role': role, 'content': content})
        else:
            ollama_messages.append({'role': role, 'content': content})
    
    return ollama_messages

def call_ollama_chat(messages, model=None, stream=False):
    """Вызов Ollama API для чата"""
    if model is None:
        model = OLLAMA_CHAT_MODEL
    
    try:
        ollama_messages = prepare_ollama_messages(messages)
        
        payload = {
            'model': model,
            'messages': ollama_messages,
            'stream': stream,
            'options': {
                'num_ctx': MODEL_CONTEXT_WINDOWS.get(model, 32768),
                'temperature': 0.7,
                'top_p': 0.9,
            }
        }
        
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=120
        )
        
        if response.status_code == 200:
            return response.json()['message']['content']
        else:
            error_msg = f"Ollama error: {response.status_code} - {response.text}"
            app.logger.error(error_msg)
            return f"⚠️ Ошибка Ollama: {response.status_code}"
            
    except requests.exceptions.ConnectionError:
        return "⚠️ Не удалось подключиться к Ollama. Проверьте, запущен ли сервис."
    except Exception as e:
        app.logger.error(f"Error calling Ollama: {str(e)}")
        return f"⚠️ Ошибка при обращении к Ollama: {str(e)}"

# -------------------------------
# Функция для автоматического выбора модели
# -------------------------------
def select_model_for_request(messages, has_images=False, has_audio=False, has_documents=False):
    """
    Автоматический подбор модели в зависимости от типа запроса
    """
    # Проверяем наличие изображений
    if has_images:
        app.logger.info(f"Выбрана мультимодальная модель: {OLLAMA_MULTIMODAL_MODEL}")
        return OLLAMA_MULTIMODAL_MODEL
    
    # Анализируем текст запроса
    last_user_message = ""
    for msg in reversed(messages):
        if msg['role'] == 'user':
            content = msg['content']
            if isinstance(content, str):
                if content.startswith('['):
                    try:
                        parts = json.loads(content)
                        for part in parts:
                            if part.get('type') == 'text':
                                last_user_message = part['text']
                                break
                    except:
                        last_user_message = content
                else:
                    last_user_message = content
            break
    
    # Ключевые слова для разных типов задач
    reasoning_keywords = [
        'почему', 'зачем', 'объясни', 'рассуждай', 'думай', 'анализируй',
        'сравни', 'спрогнозируй', 'выведи', 'логика', 'reasoning', 'analyze',
        'explain why', 'what if', 'продумай', 'рассуждение', 'умозаключение',
        'выведи формулу', 'докажи', 'доказательство', 'теорема'
    ]
    
    # Проверяем на сложные рассуждения
    last_user_message_lower = last_user_message.lower()
    if any(keyword in last_user_message_lower for keyword in reasoning_keywords):
        app.logger.info(f"Выбрана модель для рассуждений: {OLLAMA_REASONING_MODEL}")
        return OLLAMA_REASONING_MODEL
    
    # По умолчанию используем обычную чат-модель
    app.logger.info(f"Выбрана стандартная чат-модель: {OLLAMA_CHAT_MODEL}")
    return OLLAMA_CHAT_MODEL

# -------------------------------
# Анализ сообщений для определения типа контента
# -------------------------------
def analyze_messages_for_content(messages):
    """
    Анализирует сообщения на наличие изображений, аудио, документов
    """
    has_images = False
    has_audio = False
    has_documents = False
    
    for msg in messages:
        if msg.get('file_type'):
            if msg['file_type'].startswith('image/'):
                has_images = True
            elif msg['file_type'].startswith('audio/'):
                has_audio = True
            elif msg['file_type'] in ['application/pdf', 'text/plain', 
                                     'application/msword', 
                                     'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
                has_documents = True
    
    return has_images, has_audio, has_documents

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
            
            # Сеансы чатов - создаем таблицу без model_name
            c.execute('''
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    title TEXT,
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
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Проверяем наличие колонки model_name в chat_sessions
            c.execute("PRAGMA table_info(chat_sessions)")
            columns = [column[1] for column in c.fetchall()]
            
            if 'model_name' not in columns:
                app.logger.info("Добавляем колонку model_name в таблицу chat_sessions")
                c.execute('ALTER TABLE chat_sessions ADD COLUMN model_name TEXT DEFAULT "auto"')
                conn.commit()
                app.logger.info("Колонка model_name успешно добавлена")
            
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
            SELECT role, content, file_data, file_type, file_name, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC
        ''', (session_id,))
        return [dict(row) for row in c.fetchall()]

def create_session(user_id, title="Новый сеанс"):
    """Создание нового сеанса без привязки к конкретной модели"""
    session_id = str(uuid.uuid4())
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO chat_sessions (id, user_id, title, model_name)
            VALUES (?, ?, ?, ?)
        ''', (session_id, user_id, title, 'auto'))
        conn.commit()
    return session_id

def update_session_title(session_id, first_message):
    """Обновить заголовок сеанса на основе первого сообщения"""
    title = first_message[:40] + ('...' if len(first_message) > 40 else '')
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE chat_sessions
            SET title = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (title, session_id))
        conn.commit()

def save_message(session_id, role, content, file_data=None, file_type=None, file_name=None):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO messages (session_id, role, content, file_data, file_type, file_name)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (session_id, role, content, file_data, file_type, file_name))
        c.execute('''
            UPDATE chat_sessions
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (session_id,))
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
                         current_session=session.get('current_session'))

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
# Отправка сообщения (С ВРЕМЕНЕМ ОТВЕТА)
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
    
    if 'multipart/form-data' in request.content_type:
        message_text = request.form.get('message', '')
        if 'file' in request.files:
            file = request.files['file']
            if file.filename:
                file_data = base64.b64encode(file.read()).decode('utf-8')
                file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
                file_name = file.filename
    else:
        data = request.get_json()
        message_text = data.get('message', '')
    
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
    
    # Проверяем, является ли это первым сообщением в сеансе
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
        msg_count = c.fetchone()[0]
        is_first_message = (msg_count == 0)
    
    # Сохраняем сообщение пользователя
    save_message(session_id, 'user', json.dumps(user_content) if user_content else message_text,
                 file_data, file_type, file_name)
    
    # Получаем время отправки сообщения пользователя
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            SELECT timestamp FROM messages 
            WHERE session_id = ? AND role = 'user' 
            ORDER BY timestamp DESC LIMIT 1
        ''', (session_id,))
        row = c.fetchone()
        user_timestamp = row[0] if row else None
    
    # Обновляем заголовок для первого сообщения (ТЕПЕРЬ СРАЗУ ПОСЛЕ СОХРАНЕНИЯ)
    if is_first_message and message_text:
        update_session_title(session_id, message_text)
    
    # Получаем историю и анализируем контент
    history = get_session_messages(session_id)
    has_images, has_audio, has_documents = analyze_messages_for_content(history)
    
    # АВТОМАТИЧЕСКИЙ ПОДБОР МОДЕЛИ
    selected_model = select_model_for_request(history, has_images, has_audio, has_documents)
    
    app.logger.info(f"Session {session_id}: выбрана модель {selected_model}")
    
    # ЗАМЕР ВРЕМЕНИ ВЫПОЛНЕНИЯ
    start_time = time.time()
    
    # Запрос к Ollama
    bot_reply = call_ollama_chat(history, model=selected_model)
    
    end_time = time.time()
    response_time = round(end_time - start_time, 1)  # Округляем до 1 знака
    
    # Сохраняем ответ
    save_message(session_id, 'assistant', bot_reply)
    
    # Получаем timestamp сохраненного ответа
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            SELECT timestamp FROM messages 
            WHERE session_id = ? AND role = 'assistant' 
            ORDER BY timestamp DESC LIMIT 1
        ''', (session_id,))
        row = c.fetchone()
        assistant_timestamp = row[0] if row else None
    
    return jsonify({
        'response': bot_reply,
        'session_id': session_id,
        'model_used': selected_model,
        'response_time': response_time,
        'user_timestamp': user_timestamp,
        'assistant_timestamp': assistant_timestamp
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
        c.execute('UPDATE chat_sessions SET title = ? WHERE id = ?', ('Новый сеанс', session_id))
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
    return {
        'footer_content': 'ИИ Локальный v1.0 (с) 2026 Барсуков Валерий'
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)