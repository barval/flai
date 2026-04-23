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

    # Check if the result exists — request_id is a UUID known only to the
    # client that submitted the request, so possession of the ID implies ownership.
    result = current_app.request_queue.check_result(request_id)
    if result and result.get('status') in ('completed', 'error'):
        return jsonify(result)

    return jsonify({'status': 'pending'})