from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
import os
import sqlite3
import json
import base64
from datetime import datetime, timezone
from dotenv import load_dotenv
import mimetypes
import uuid
import requests  # добавлен импорт

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# -------------------------------
# Настройка путей к БД
# -------------------------------
# Создаем папку для данных, если её нет
DATA_DIR = 'data'
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

CHAT_DB_PATH = os.path.join(DATA_DIR, 'chat.db')

# -------------------------------
# Инициализация БД
# -------------------------------
def init_db():
    """Инициализация базы данных"""
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
            conn.commit()
            app.logger.info(f"Database initialized successfully at {CHAT_DB_PATH}")
    except Exception as e:
        app.logger.error(f"Failed to initialize database: {str(e)}")
        raise

# Инициализируем БД при старте
init_db()

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
        # Создаем тестового пользователя, если файла нет
        app.logger.warning("users.list not found, creating default user")
        users['admin@local.com'] = {'password': 'admin123'}
    return users

USERS = load_users()

def get_user_sessions(user_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT id, title, created_at, updated_at
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
    session_id = str(uuid.uuid4())
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO chat_sessions (id, user_id, title)
            VALUES (?, ?, ?)
        ''', (session_id, user_id, title))
        conn.commit()
    return session_id

def update_session_title(session_id, first_message):
    """Обновить заголовок сеанса на основе первого сообщения (до 30 символов)"""
    title = first_message[:30] + ('...' if len(first_message) > 30 else '')
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
    
    # Восстанавливаем последний сеанс
    if not session.get('current_session'):
        last_id = get_last_session(user_id)
        if last_id and any(s['id'] == last_id for s in sessions):
            session['current_session'] = last_id
        elif sessions:
            session['current_session'] = sessions[0]['id']
        else:
            # Создаём первый сеанс
            new_id = create_session(user_id)
            session['current_session'] = new_id
            sessions = get_user_sessions(user_id)  # обновляем список
    
    return render_template('chat.html', sessions=sessions, current_session=session.get('current_session'))

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
# Эмуляция локального ИИ
# -------------------------------
@app.route('/v1/chat/completions', methods=['POST'])
def local_completion():
    """
    Заглушка для локальной модели.
    В реальном проекте здесь будет вызов llama.cpp, Ollama, transformers и т.д.
    """
    data = request.get_json()
    messages = data.get('messages', [])
    
    # Простейшая заглушка – берём последнее сообщение пользователя
    last_user_msg = ''
    for msg in reversed(messages):
        if msg['role'] == 'user':
            if isinstance(msg.get('content'), list):
                for part in msg['content']:
                    if part.get('type') == 'text':
                        last_user_msg = part['text']
                        break
            else:
                last_user_msg = msg.get('content', '')
            break
    
    response_text = f"Вы сказали: «{last_user_msg}»\n\n(это заглушка локальной модели. Подключите реальный эндпоинт LLM.)"
    
    return jsonify({
        'choices': [{
            'message': {
                'role': 'assistant',
                'content': response_text
            }
        }]
    })

# -------------------------------
# Отправка сообщения
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
    
    # Проверяем, multipart или JSON
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
        # Пока не поддерживаем base64‑файлы через JSON (можно добавить)
    
    # Сохраняем сообщение пользователя
    user_content = []
    if message_text:
        user_content.append({"type": "text", "text": message_text})
    if file_data:
        user_content.append({"type": "file", "file_data": file_data, "file_type": file_type, "file_name": file_name})
    
    save_message(session_id, 'user', json.dumps(user_content) if user_content else message_text,
                 file_data, file_type, file_name)
    
    # Проверяем, нужно ли обновить заголовок (если это первое сообщение в сеансе)
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
        msg_count = c.fetchone()[0]
        if msg_count == 1 and message_text:
            update_session_title(session_id, message_text)
    
    # Формируем историю для модели
    history = get_session_messages(session_id)
    openrouter_messages = []
    for msg in history:
        role = msg['role']
        content = json.loads(msg['content']) if msg['content'].startswith('[') else msg['content']
        openrouter_messages.append({"role": role, "content": content})
    
    # Запрос к локальному эндпоинту
    try:
        # Используем requests для вызова своего же API
        resp = requests.post(
            'http://localhost:5000/v1/chat/completions',
            json={'messages': openrouter_messages},
            timeout=60
        )
        resp.raise_for_status()
        bot_reply = resp.json()['choices'][0]['message']['content']
    except Exception as e:
        app.logger.error(f"Error calling local model: {str(e)}")
        bot_reply = f"⚠️ Ошибка при обращении к локальной модели: {str(e)}"
    
    # Сохраняем ответ
    save_message(session_id, 'assistant', bot_reply)
    
    return jsonify({
        'response': bot_reply,
        'session_id': session_id
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
    
    # Проверяем, что сеанс принадлежит пользователю
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT user_id FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        
        if not row:
            return jsonify({'error': 'Сеанс не найден'}), 404
        
        if row[0] != user_id:
            return jsonify({'error': 'Нет прав на удаление этого сеанса'}), 403
        
        # Удаляем сообщения сеанса
        c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
        
        # Удаляем сам сеанс
        c.execute('DELETE FROM chat_sessions WHERE id = ?', (session_id,))
        
        # Если это был последний сеанс пользователя, удаляем запись о последнем сеансе
        c.execute('SELECT COUNT(*) FROM chat_sessions WHERE user_id = ?', (user_id,))
        count = c.fetchone()[0]
        
        if count == 0:
            c.execute('DELETE FROM user_sessions WHERE user_id = ?', (user_id,))
        else:
            # Если удалили текущий сеанс, обновим last_session_id
            c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = ?', (user_id,))
            row = c.fetchone()
            if row and row[0] == session_id:
                # Получаем первый доступный сеанс
                c.execute('SELECT id FROM chat_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1', (user_id,))
                new_last = c.fetchone()
                if new_last:
                    c.execute('UPDATE user_sessions SET last_session_id = ? WHERE user_id = ?', (new_last[0], user_id))
                else:
                    c.execute('DELETE FROM user_sessions WHERE user_id = ?', (user_id,))
        
        conn.commit()
    
    # Если удалили текущий сеанс, очищаем его из сессии
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
        'footer_content': 'ИИ Локальный v1.0 (с) 2026 Барсуков Валерий & DeepSeek V3'
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)