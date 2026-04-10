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

    # First check if the result already exists — return it regardless of ownership
    # (ownership is removed when task completes, but the result should still be accessible)
    result = current_app.request_queue.check_result(request_id)
    if result and result.get('status') in ('completed', 'error'):
        return jsonify(result)

    # No deserialized result — check raw data and ownership
    user_requests_key = current_app.request_queue.user_requests_key
    is_owner = current_app.request_queue.redis.sismember(f"{user_requests_key}:{user_id}", request_id)

    # Check if raw result data exists even if deserialization failed
    raw = current_app.request_queue.redis.hget(current_app.request_queue.results_key, request_id)
    if raw and not is_owner:
        # Result exists but we can't deserialize it — return a generic completed status
        # so the client stops polling instead of getting stuck on 404
        current_app.logger.warning(
            f"Raw result exists for {request_id} but deserialization failed. "
            f"Returning placeholder to stop polling."
        )
        return jsonify({
            'status': 'completed',
            'result': {
                'session_id': None,
                'error': 'Result data corrupted'
            }
        })

    if not is_owner:
        return jsonify({'error': _('Not found')}), 404

    return jsonify({'status': 'pending'})