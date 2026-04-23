# app/routes/sessions.py
from flask import Blueprint, request, session, jsonify, current_app
from flask_babel import gettext as _
from app import db
from app.database import get_db
from app.utils import get_current_time_in_timezone_for_db, validate_session_ownership

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

    if not validate_session_ownership(session_id, user_id):
        current_app.logger.warning(f"User {user_id} attempted to access session {session_id}")
        return jsonify({'error': _('Session not found or access denied')}), 404

    prev_session = session.get('current_session')
    if prev_session and prev_session != session_id:
        db.update_session_visit(user_id, prev_session)

    session['current_session'] = session_id
    db.set_last_session(user_id, session_id)
    db.update_session_visit(user_id, session_id)
    return jsonify({'status': 'ok'})


@bp.route('/sessions/<session_id>/model-info', methods=['GET'])
def api_get_session_model(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401

    if not validate_session_ownership(session_id, session['login']):
        current_app.logger.warning(f"User {session['login']} attempted to access session {session_id}")
        return jsonify({'error': _('Session not found')}), 404

    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT model_name FROM chat_sessions WHERE id = %s', (session_id,))
        row = c.fetchone()
        return jsonify({'model_name': row['model_name'] if row else 'auto'})


@bp.route('/sessions/new', methods=['POST'])
def api_new_session():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    lang = session.get('language', 'ru')
    session_id = db.create_session(session['login'], lang=lang)
    session['current_session'] = session_id
    db.set_last_session(session['login'], session_id)
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT title FROM chat_sessions WHERE id = %s', (session_id,))
        title = c.fetchone()['title']
    return jsonify({'id': session_id, 'title': title})


@bp.route('/sessions/<session_id>/update-title', methods=['POST'])
def api_update_session_title(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401

    if not validate_session_ownership(session_id, session['login']):
        current_app.logger.warning(f"User {session['login']} attempted to update session {session_id}")
        return jsonify({'error': _('Session not found')}), 404

    data = request.get_json()
    new_title = data.get('title', _('New session'))
    current_time = get_current_time_in_timezone_for_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE chat_sessions
            SET title = %s, updated_at = %s
            WHERE id = %s AND user_id = %s
        ''', (new_title, current_time, session_id, session['login']))
    return jsonify({'status': 'ok', 'title': new_title})


@bp.route('/sessions/<session_id>/delete', methods=['POST'])
def api_delete_session(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401

    if not validate_session_ownership(session_id, session['login']):
        current_app.logger.warning(f"User {session['login']} attempted to delete session {session_id}")
        return jsonify({'error': _('Session not found or access denied')}), 404

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

    if not validate_session_ownership(session_id, session['login']):
        return jsonify({'error': _('Session not found')}), 404

    db.update_session_visit(session['login'], session_id)
    return jsonify({'status': 'ok'})


@bp.route('/clear_history', methods=['POST'])
def clear_history():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    session_id = session.get('current_session')
    if not session_id:
        return jsonify({'error': _('No active session')}), 400

    if not validate_session_ownership(session_id, session['login']):
        current_app.logger.warning(f"User {session['login']} attempted to clear history of session {session_id}")
        return jsonify({'error': _('Session not found')}), 404

    with get_db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM messages WHERE session_id = %s', (session_id,))
        current_time = get_current_time_in_timezone_for_db()
        c.execute('UPDATE chat_sessions SET title = %s, updated_at = %s WHERE id = %s',
                 (_('New session'), current_time, session_id))
    return jsonify({'status': 'ok'})
