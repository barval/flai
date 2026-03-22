# app/socket_events.py
from flask_socketio import emit, disconnect
from flask import session, request
from . import socketio
import logging

logger = logging.getLogger(__name__)

@socketio.on('connect')
def handle_connect():
    """Client connected, store user_id in session for namespace."""
    if 'login' in session:
        logger.info(f"WebSocket connected for user {session['login']}")
        # Join a room named after user_id to send targeted messages
        socketio.server.enter_room(request.sid, f"user_{session['login']}")
        emit('connected', {'message': 'Connected'})
    else:
        logger.warning("Unauthenticated WebSocket connection attempt")
        disconnect()

@socketio.on('disconnect')
def handle_disconnect():
    if 'login' in session:
        logger.info(f"WebSocket disconnected for user {session['login']}")

def emit_queue_status(user_id, data):
    """Emit queue status update to specific user."""
    socketio.emit('queue_status', data, room=f"user_{user_id}")

def emit_session_update(user_id, session_data):
    """Emit session list update to specific user."""
    socketio.emit('sessions_update', session_data, room=f"user_{user_id}")

def emit_new_message(user_id, message_data):
    """Emit new message notification to specific user."""
    socketio.emit('new_message', message_data, room=f"user_{user_id}")

def emit_transcribing_status(user_id, session_id, is_transcribing):
    """Emit transcribing status update for a session."""
    socketio.emit('transcribing_status', {
        'session_id': session_id,
        'is_transcribing': is_transcribing
    }, room=f"user_{user_id}")