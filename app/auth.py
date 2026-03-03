import os
import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app

logger = logging.getLogger(__name__)
bp = Blueprint('auth', __name__)

def load_users():
    """Загружает пользователей из users.list."""
    users = {}
    users_file = 'users.list'
    if not os.path.exists(users_file):
        logger.error("users.list not found")
        return users
    with open(users_file, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) >= 2:
                email = parts[0].strip()
                password = parts[1].strip()
                service_class = int(parts[2].strip()) if len(parts) >= 3 else 2
                if email and password and service_class in [0,1,2]:
                    users[email] = {'password': password, 'service_class': service_class}
                else:
                    logger.error(f"Некорректные данные в строке {line_num}")
            else:
                logger.error(f"Некорректная строка {line_num}")
    return users

USERS = load_users()

@bp.route('/login', methods=['GET', 'POST'])
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
            return redirect(url_for('chat.chat'))
        else:
            return render_template('login.html', error='Неверный email или пароль')
    return render_template('login.html')

@bp.route('/logout')
def logout():
    if 'email' in session and 'current_session' in session:
        from .db import set_last_session
        set_last_session(session['email'], session['current_session'])
    session.clear()
    return redirect(url_for('auth.login'))