import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app
from werkzeug.security import check_password_hash
from .userdb import get_user_by_login, init_user_db

logger = logging.getLogger(__name__)
bp = Blueprint('auth', __name__)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form.get('login')
        password = request.form.get('password')
        if not login_input or not password:
            return render_template('login.html', error='Все поля обязательны')

        user = get_user_by_login(login_input)
        if user and user['is_active'] and check_password_hash(user['password_hash'], password):
            session['login'] = user['login']
            session['name'] = user['name']
            session['service_class'] = user['service_class']
            session['is_admin'] = user['is_admin']
            session['user_id'] = user['login']   # для совместимости с chat.db
            return redirect(url_for('chat.chat'))
        else:
            return render_template('login.html', error='Неверный логин или пароль')
    return render_template('login.html')

@bp.route('/logout')
def logout():
    if 'login' in session and 'current_session' in session:
        from .db import set_last_session
        set_last_session(session['login'], session['current_session'])
    session.clear()
    return redirect(url_for('auth.login'))