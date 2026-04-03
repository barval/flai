# app/routes/queue.py
from flask import Blueprint, session, jsonify, current_app
from flask_babel import gettext as _

bp = Blueprint('queue', __name__, url_prefix='/api/queue')

@bp.route('/status', methods=['GET'])
def api_queue_status():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    user_id = session['login']
    lang = session.get('language', 'ru')
    status = current_app.request_queue.get_user_requests_status(user_id, lang=lang)
    queue_length = current_app.request_queue.redis.llen(current_app.request_queue.queue_key)
    status['system'] = {
        'total_queued': queue_length,
        'current_load': 'high' if queue_length > 10 else 'normal',
        'avg_response_time': 5
    }
    return jsonify(status)

@bp.route('/counts', methods=['GET'])
def api_queue_counts():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    user_id = session['login']
    user_queued, total_queued = current_app.request_queue.get_user_queue_counts(user_id)
    return jsonify({'user_queued': user_queued, 'total_queued': total_queued})

@bp.route('/result/<request_id>', methods=['GET'])
def api_check_result(request_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    user_id = session['login']

    # First check if the result already exists
    result = current_app.request_queue.check_result(request_id)
    if result and result.get('status') in ('completed', 'error'):
        return jsonify(result)

    # No result yet — verify this request belongs to the current user
    # (ownership is removed from the set when the task completes, so we only
    # check ownership while the task is still pending)
    user_requests_key = current_app.request_queue.user_requests_key
    if not current_app.request_queue.redis.sismember(f"{user_requests_key}:{user_id}", request_id):
        return jsonify({'error': _('Not found')}), 404

    return jsonify({'status': 'pending'})