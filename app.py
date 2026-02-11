from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
import os
import requests
import json
import base64
import time
import sqlite3
import re
from datetime import datetime, timezone
from dotenv import load_dotenv
from jinja2 import Environment

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
app.config['JSON_AS_ASCII'] = False

# Константы
DEFAULT_MAX_TOKENS = 2048
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
API_BASE_URL = "https://openrouter.ai/api/v1"
MODEL_ID_REGEX = r'^[\w\-.:/]+$'  # Разрешаем буквы, цифры, -, ., :, /

def init_db():
    with sqlite3.connect('chat.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_histories (
                user_id TEXT,
                model_id TEXT,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, model_id, timestamp)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_sessions (
                user_id TEXT PRIMARY KEY,
                last_model_id TEXT
            )
        ''')
        conn.commit()

def init_models_db():
    with sqlite3.connect('models.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='models'")
        if not cursor.fetchone():
            cursor.execute('''
                CREATE TABLE models (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    images INTEGER,
                    context INTEGER,
                    max_tokens INTEGER
                )
            ''')
            conn.commit()

init_db()
init_models_db()

def load_users():
    users = {}
    if os.path.exists('users.list'):
        with open('users.list', 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    email, password, api_key = line.strip().split(',')
                    users[email] = {'password': password, 'api_key': api_key}
    return users

USERS = load_users()

def check_api_availability():
    try:
        response = requests.get(f"{API_BASE_URL}/models", timeout=5)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False

def validate_model_id(model_id):
    """Более мягкая валидация ID моделей"""
    if not model_id or not isinstance(model_id, str):
        return False
    return bool(re.match(MODEL_ID_REGEX, model_id))

def get_chat_history(user_id, model_id):
    with sqlite3.connect('chat.db') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT role, content, timestamp FROM chat_histories 
            WHERE user_id = ? AND model_id = ?
            ORDER BY timestamp
        ''', (user_id, model_id))
        rows = cursor.fetchall()
        messages = []
        for row in rows:
            try:
                content = row['content']
                if row['role'] == "assistant":
                    try:
                        content_data = json.loads(content) if isinstance(content, str) else content
                    except json.JSONDecodeError:
                        content_data = content
                else:
                    if isinstance(content, str):
                        try:
                            content_data = json.loads(content)
                            if not isinstance(content_data, list):
                                content_data = [{"type": "text", "text": content}]
                        except json.JSONDecodeError:
                            content_data = [{"type": "text", "text": content}]
                    else:
                        content_data = content

                messages.append({
                    "role": row['role'],
                    "content": content_data,
                    "timestamp": row['timestamp']
                })
            except Exception as e:
                app.logger.error(f"Error processing message: {str(e)}")
                messages.append({
                    "role": row['role'],
                    "content": content,
                    "timestamp": row['timestamp']
                })
        return messages

def save_message(user_id, model_id, role, content):
    with sqlite3.connect('chat.db') as conn:
        cursor = conn.cursor()
        if role == "assistant":
            content_str = json.dumps(content) if not isinstance(content, str) else content
        else:
            if isinstance(content, list):
                content_str = json.dumps(content)
            else:
                content_str = content if isinstance(content, str) else json.dumps(content)
        
        utc_now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            INSERT INTO chat_histories (user_id, model_id, role, content, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, model_id, role, content_str, utc_now))
        
        cursor.execute('''
            INSERT OR REPLACE INTO user_sessions (user_id, last_model_id)
            VALUES (?, ?)
        ''', (user_id, model_id))
        conn.commit()

def clear_chat_history(user_id, model_id):
    with sqlite3.connect('chat.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM chat_histories 
            WHERE user_id = ? AND model_id = ?
        ''', (user_id, model_id))
        conn.commit()

def get_last_model(user_id):
    with sqlite3.connect('chat.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT last_model_id FROM user_sessions 
            WHERE user_id = ?
        ''', (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None

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
            return render_template('login.html', error='Все поля обязательны для заполнения')
        
        if email in USERS and USERS[email]['password'] == password:
            session['email'] = email
            session['api_key'] = USERS[email]['api_key']
            return redirect(url_for('chat'))
        else:
            return render_template('login.html', error='Неверный email или пароль')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'email' in session:
        email = session['email']
        if 'current_model' in session:
            with sqlite3.connect('chat.db') as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO user_sessions (user_id, last_model_id)
                    VALUES (?, ?)
                ''', (email, session['current_model']))
                conn.commit()
    
    session.pop('email', None)
    session.pop('api_key', None)
    session.pop('current_model', None)
    return redirect(url_for('login'))

@app.route('/chat')
def chat():
    if 'email' not in session:
        return redirect(url_for('login'))
    
    email = session['email']
    last_model = get_last_model(email)
    
    if last_model:
        session['current_model'] = last_model
    
    models = {}
    try:
        with sqlite3.connect('models.db') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM models ORDER BY name')
            for row in cursor.fetchall():
                models[row['id']] = dict(row)
    except sqlite3.OperationalError:
        return render_template('chat.html', require_update=True, models={})
    
    if not models:
        return render_template('chat.html', require_update=True, models={})
    
    return render_template('chat.html', require_update=False, models=models)

@app.route('/get_models', methods=['GET'])
def get_models():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        models = {}
        with sqlite3.connect('models.db') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM models ORDER BY name')
            for row in cursor.fetchall():
                models[row['id']] = dict(row)
        
        if not models:
            return jsonify({'error': 'Модели не загружены', 'require_update': True}), 404
            
        return jsonify(models)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/update_models', methods=['POST'])
def update_models():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        if not check_api_availability():
            return jsonify({'error': 'API OpenRouter недоступно'}), 503

        init_models_db()

        headers = {
            "Authorization": f"Bearer {session['api_key']}",
            "HTTP-Referer": os.getenv('DOMAIN_URL', 'http://localhost:5000'),
            "X-Title": "AI Local"
        }
        
        response = requests.get(
            f"{API_BASE_URL}/models",
            headers=headers,
            timeout=30
        )
        
        if response.status_code != 200:
            return jsonify({
                'error': f"Ошибка API OpenRouter (код {response.status_code})",
                'details': response.text
            }), response.status_code
        
        data = response.json()
        free_models = []
        
        for model in data.get('data', []):
            if "(free)" in model.get('name', '').lower():
                supports_images = "image" in model.get('architecture', {}).get('input_modalities', [])
                top_provider = model.get('top_provider', {})
                
                max_tokens = top_provider.get('max_completion_tokens')
                if max_tokens is None:
                    max_tokens = DEFAULT_MAX_TOKENS
                
                free_models.append((
                    model['id'],
                    model['name'],
                    model.get('description', ''),
                    1 if supports_images else 0,
                    top_provider.get('context_length', 0),
                    max_tokens
                ))
        
        with sqlite3.connect('models.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='models'")
            if cursor.fetchone():
                cursor.execute('DELETE FROM models')
            else:
                cursor.execute('''
                    CREATE TABLE models (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        description TEXT,
                        images INTEGER,
                        context INTEGER,
                        max_tokens INTEGER
                    )
                ''')
            
            cursor.executemany('''
                INSERT INTO models (id, name, description, images, context, max_tokens)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', free_models)
            conn.commit()
        
        models = {}
        with sqlite3.connect('models.db') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM models ORDER BY name')
            for row in cursor.fetchall():
                models[row['id']] = dict(row)
        
        return jsonify({
            'status': 'success', 
            'count': len(free_models),
            'models': models
        })
    
    except Exception as e:
        app.logger.error(f"Update models error: {str(e)}", exc_info=True)
        return jsonify({
            'error': str(e),
            'details': 'Попробуйте обновить страницу и повторить попытку'
        }), 500

@app.route('/send_message', methods=['POST'])
def send_message():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401

    start_time = time.time()
    user_id = session['email']
    
    try:
        # Проверяем доступность API
        if not check_api_availability():
            return jsonify({'error': 'API OpenRouter недоступно'}), 503

        # Определяем тип контента и получаем данные
        if 'multipart/form-data' in request.content_type:
            model_id = request.form.get('model')
            message = request.form.get('message')
            file = request.files.get('file')
            
            # Проверяем размер изображения
            if file and file.content_length > MAX_IMAGE_SIZE:
                return jsonify({'error': 'Размер изображения превышает 5MB'}), 400
                
            encoded_image = base64.b64encode(file.read()).decode('utf-8') if file and file.filename else None
        else:
            data = request.get_json()
            model_id = data.get('model')
            message = data.get('message')
            encoded_image = data.get('image')

        # Мягкая проверка model_id
        if not model_id or not isinstance(model_id, str):
            return jsonify({'error': 'Не указан ID модели'}), 400

        # Проверяем поддержку изображений
        with sqlite3.connect('models.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT images FROM models WHERE id = ?', (model_id,))
            result = cursor.fetchone()
            if not result:
                return jsonify({'error': 'Модель не найдена'}), 404
            model_supports_images = result[0] == 1

        if encoded_image and not model_supports_images:
            return jsonify({'error': 'Выбранная модель не поддерживает изображения'}), 400

        session['current_model'] = model_id
        
        # Формируем сообщение
        messages = get_chat_history(user_id, model_id)
        new_message = {"role": "user", "content": []}
        
        if encoded_image:
            new_message["content"].append({
                "type": "image_url",
                "image_url": f"data:image/jpeg;base64,{encoded_image}"
            })
        if message:
            new_message["content"].append({
                "type": "text",
                "text": message
            })
        
        save_message(user_id, model_id, "user", new_message["content"])

        # Получаем max_tokens для модели
        with sqlite3.connect('models.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT max_tokens FROM models WHERE id = ?', (model_id,))
            max_tokens = cursor.fetchone()[0] or DEFAULT_MAX_TOKENS

        # Формируем запрос к API
        headers = {
            "Authorization": f"Bearer {session['api_key']}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv('DOMAIN_URL', 'http://localhost:5000'),
            "X-Title": "AI Local"
        }

        payload = {
            "model": model_id,
            "messages": messages + [new_message],
            "temperature": 0.7,
            "max_tokens": max_tokens
        }

        # Отправляем запрос
        response = requests.post(
            f"{API_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )

        # Проверяем Content-Type ответа
        if 'application/json' not in response.headers.get('Content-Type', ''):
            error_html = response.text[:500]
            app.logger.error(f"Non-JSON response: {error_html}")
            return jsonify({
                'error': 'Некорректный ответ от сервера',
                'details': 'Сервер вернул не-JSON ответ'
            }), 500

        response_data = response.json()

        if response.status_code != 200:
            error_msg = response_data.get('error', {}).get('message', 'Неизвестная ошибка API')
            app.logger.error(f"API Error: {error_msg}")
            return jsonify({
                'error': f"Ошибка API (код {response.status_code})",
                'details': error_msg
            }), response.status_code

        if not response_data.get('choices'):
            return jsonify({
                'error': 'Некорректный ответ от API',
                'details': 'Ответ не содержит данных'
            }), 500

        # Обработка успешного ответа
        bot_response = response_data['choices'][0]['message']['content']
        response_time = round(time.time() - start_time, 1)
        
        save_message(user_id, model_id, "assistant", bot_response)

        return jsonify({
            'response': bot_response,
            'model': model_id,
            'response_time': response_time
        })

    except requests.exceptions.Timeout:
        return jsonify({'error': 'Таймаут запроса', 'details': 'Модель не ответила в течение 60 секунд'}), 504
    except json.JSONDecodeError as e:
        app.logger.error(f"JSON decode error: {str(e)}")
        return jsonify({
            'error': 'Ошибка обработки ответа',
            'details': 'Сервер вернул невалидный JSON'
        }), 500
    except Exception as e:
        app.logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Внутренняя ошибка',
            'details': str(e)
        }), 500

@app.route('/get_chat_history', methods=['POST'])
def get_chat_history_route():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401

    try:
        data = request.get_json()
        model_id = data.get('model')
        user_id = session['email']
        
        if not model_id:
            return jsonify({'error': 'Не указана модель'}), 400
            
        # Убираем строгую валидацию для этого запроса
        messages = get_chat_history(user_id, model_id)
        return jsonify(messages)
    except Exception as e:
        app.logger.error(f"Get chat history error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Ошибка при загрузке истории', 'details': str(e)}), 500

@app.route('/clear_history', methods=['POST'])
def clear_history():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401

    try:
        data = request.get_json()
        model_id = data.get('model')
        user_id = session['email']
        
        if not model_id or not validate_model_id(model_id):
            return jsonify({'error': 'Не указана или некорректна модель'}), 400
            
        clear_chat_history(user_id, model_id)
        
        return jsonify({'status': 'success', 'model': model_id})
    except Exception as e:
        app.logger.error(f"Clear history error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Ошибка при очистке истории', 'details': str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                             'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.context_processor
def inject_footer_content():
    return {
        'footer_content': 'ИИ МультиЧат v7.6 (с) 2025 Барсуков Валерий & DeepSeek V3'
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)