# app/routes/sessions.py
import sqlite3
from flask import Blueprint, request, session, jsonify, current_app
from flask_babel import gettext as _
from app import db
from app.utils import get_current_time_in_timezone_for_db

bp = Blueprint('sessions', __name__, url_prefix='/api')

@bp.route('/sessions', methods=['GET'])
def api_get_sessions():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    return jsonify(db.get_user_sessions(session['login']))

@bp.route('/sessions/<session_id>/switch', methods=['POST'])
def api_switch_session(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    user_id = session['login']
    session['current_session'] = session_id
    db.set_last_session(user_id, session_id)
    db.update_session_visit(user_id, session_id)
    return jsonify({'status': 'ok'})

@bp.route('/sessions/<session_id>/model-info', methods=['GET'])
def api_get_session_model(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    with sqlite3.connect(db.CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT model_name FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        return jsonify({'model_name': row[0] if row else 'auto'})

@bp.route('/sessions/new', methods=['POST'])
def api_new_session():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    lang = session.get('language', 'ru')
    session_id = db.create_session(session['login'], lang=lang)
    session['current_session'] = session_id
    db.set_last_session(session['login'], session_id)
    with sqlite3.connect(db.CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT title FROM chat_sessions WHERE id = ?', (session_id,))
        title = c.fetchone()[0]
    return jsonify({'id': session_id, 'title': title})

@bp.route('/sessions/<session_id>/update-title', methods=['POST'])
def api_update_session_title(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    data = request.get_json()
    new_title = data.get('title', _('New session'))
    current_time = get_current_time_in_timezone_for_db()
    with sqlite3.connect(db.CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE chat_sessions
            SET title = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
        ''', (new_title, current_time, session_id, session['login']))
        conn.commit()
    return jsonify({'status': 'ok', 'title': new_title})

@bp.route('/sessions/<session_id>/delete', methods=['POST'])
def api_delete_session(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    success = db.delete_session_and_messages(
        session_id,
        session['login'],
        upload_folder=current_app.config['UPLOAD_FOLDER']
    )
    if not success:
        return jsonify({'error': _('Permission denied or session not found')}), 403
    if session.get('current_session') == session_id:
        session.pop('current_session', None)
    return jsonify({'status': 'ok'})

@bp.route('/sessions/<session_id>/visit', methods=['POST'])
def api_update_session_visit(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    db.update_session_visit(session['login'], session_id)
    return jsonify({'status': 'ok'})

@bp.route('/clear_history', methods=['POST'])
def clear_history():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    session_id = session.get('current_session')
    if not session_id:
        return jsonify({'error': _('No active session')}), 400
    with sqlite3.connect(db.CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
        current_time = get_current_time_in_timezone_for_db()
        c.execute('UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?',
                 (_('New session'), current_time, session_id))
        conn.commit()
    return jsonify({'status': 'ok'})