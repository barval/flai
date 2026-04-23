# app/routes/auth.py
import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app, jsonify, make_response
from werkzeug.security import check_password_hash
from flask_limiter.errors import RateLimitExceeded
from app import csrf, limiter
from app.userdb import get_user_by_login, update_user
from flask_babel import gettext as _

logger = logging.getLogger(__name__)
bp = Blueprint('auth', __name__)


@bp.errorhandler(RateLimitExceeded)
def handle_rate_limit(error):
    """Handle rate limit exceeded errors."""
    if request.path.startswith('/api/'):
        return jsonify({'error': _('Too many attempts. Please try again later.')}), 429
    response = make_response(render_template('login.html', error=_('Too many attempts. Please try again later.')))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response, 429


@bp.route('/login', methods=['GET', 'POST'])
@csrf.exempt
@limiter.limit("5 per minute;10 per hour", key_func=lambda: request.form.get('login') or request.remote_addr, methods=['POST'])
def login():
    if request.method == 'POST':
        login_input = request.form.get('login')
        password = request.form.get('password')
        theme = request.form.get('theme', 'light')

        if not login_input or not password:
            logger.warning(f"Login attempt with missing fields from {request.remote_addr}")
            response = make_response(render_template('login.html', error=_('All fields are required')))
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return response

        user = get_user_by_login(login_input)
        if user and user['is_active'] and check_password_hash(user['password_hash'], password):
            logger.info(f"Successful login: {login_input} from {request.remote_addr}")
            
            if user['theme'] != theme:
                update_user(login_input, theme=theme)

            session['login'] = user['login']
            session['name'] = user['name']
            session['service_class'] = user['service_class']
            session['is_admin'] = user['is_admin']
            session['user_id'] = user['login']
            session['language'] = user['language']
            session['voice_gender'] = user['voice_gender']
            session['theme'] = theme

            if user['is_admin']:
                return redirect(url_for('admin.admin_panel'))
            else:
                return redirect(url_for('chat.chat'))
        else:
            logger.warning(f"Failed login attempt: {login_input} from {request.remote_addr}")
            response = make_response(render_template('login.html', error=_('Invalid login or password')))
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return response
    response = make_response(render_template('login.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@bp.route('/logout', methods=['POST'])
def logout():
    if 'login' in session and 'current_session' in session:
        from app.db import set_last_session
        set_last_session(session['login'], session['current_session'])
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/set-language/<lang>', methods=['POST'])
def set_language(lang):
    if lang not in ['ru', 'en']:
        return jsonify({'error': _('Invalid language')}), 400
    session['language'] = lang
    if 'login' in session:
        update_user(session['login'], language=lang)
    return jsonify({'success': True})


@bp.route('/set-voice-gender/<gender>', methods=['POST'])
def set_voice_gender(gender):
    if gender not in ['male', 'female']:
        return jsonify({'error': _('Invalid voice gender')}), 400
    session['voice_gender'] = gender
    if 'login' in session:
        update_user(session['login'], voice_gender=gender)
    return jsonify({'success': True})


@bp.route('/set-theme/<theme>', methods=['POST'])
def set_theme(theme):
    if theme not in ['light', 'dark']:
        return jsonify({'error': _('Invalid theme')}), 400
    session['theme'] = theme
    if 'login' in session:
        update_user(session['login'], theme=theme)
    return jsonify({'success': True})