# app/auth.py
import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app
from werkzeug.security import check_password_hash
from .userdb import get_user_by_login, update_user
from flask_babel import gettext as _  # noqa

logger = logging.getLogger(__name__)
bp = Blueprint('auth', __name__)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form.get('login')
        password = request.form.get('password')
        theme = request.form.get('theme', 'light')  # Get theme from hidden field

        if not login_input or not password:
            return render_template('login.html', error=_('All fields are required'))

        user = get_user_by_login(login_input)
        if user and user['is_active'] and check_password_hash(user['password_hash'], password):
            # Update user's theme preference if it changed
            if user['theme'] != theme:
                update_user(login_input, theme=theme)

            # Set session variables from user data
            session['login'] = user['login']
            session['name'] = user['name']
            session['service_class'] = user['service_class']
            session['is_admin'] = user['is_admin']
            session['user_id'] = user['login']
            session['language'] = user['language']
            session['voice_gender'] = user['voice_gender']
            session['theme'] = theme  # Use the theme from form (updated)

            if user['is_admin']:
                return redirect(url_for('admin.admin_panel'))
            else:
                return redirect(url_for('chat.chat'))
        else:
            return render_template('login.html', error=_('Invalid login or password'))
    return render_template('login.html')

@bp.route('/logout')
def logout():
    if 'login' in session and 'current_session' in session:
        from .db import set_last_session
        set_last_session(session['login'], session['current_session'])
    session.clear()
    return redirect(url_for('auth.login'))

@bp.route('/set-language/<lang>')
def set_language(lang):
    # Allow setting language for both authenticated and anonymous users
    if lang in ['ru', 'en']:
        session['language'] = lang
        # If user is logged in, update database
        if 'login' in session:
            update_user(session['login'], language=lang)
    response = redirect(request.referrer or url_for('auth.login'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@bp.route('/set-voice-gender/<gender>')
def set_voice_gender(gender):
    if gender in ['male', 'female']:
        session['voice_gender'] = gender
        if 'login' in session:
            update_user(session['login'], voice_gender=gender)
    response = redirect(request.referrer or url_for('auth.login'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@bp.route('/set-theme/<theme>')
def set_theme(theme):
    if theme in ['light', 'dark']:
        session['theme'] = theme
        if 'login' in session:
            update_user(session['login'], theme=theme)
    response = redirect(request.referrer or url_for('auth.login'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response