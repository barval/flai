from flask import Blueprint, session, jsonify, current_app

bp = Blueprint('queue', __name__)

@bp.route('/api/queue/status', methods=['GET'])
def api_queue_status():
    if 'login' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
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

@bp.route('/api/queue/counts', methods=['GET'])
def api_queue_counts():
    if 'login' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user_id = session['login']
    user_queued, total_queued = current_app.request_queue.get_user_queue_counts(user_id)
    return jsonify({'user_queued': user_queued, 'total_queued': total_queued})

@bp.route('/api/queue/result/<request_id>', methods=['GET'])
def api_check_result(request_id):
    if 'login' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    result = current_app.request_queue.check_result(request_id)
    if result:
        return jsonify(result)
    else:
        return jsonify({'status': 'pending'})