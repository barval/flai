# app/routes/messages.py
import sqlite3
import json
import base64
import time
import mimetypes
import os
import uuid
from datetime import datetime
from flask import Blueprint, request, session, jsonify, current_app
from flask_babel import gettext as _, force_locale
from app import db
from app.utils import get_current_time_in_timezone, get_current_time_in_timezone_for_db, resize_image_if_needed, save_uploaded_file, validate_session_ownership

bp = Blueprint('messages', __name__, url_prefix='/api')


@bp.route('/sessions/<session_id>/messages', methods=['GET'])
def api_get_messages(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401

    # Security: Verify session belongs to user
    if not validate_session_ownership(session_id, session['login']):
        current_app.logger.warning(f"User {session['login']} attempted to access messages in session {session_id}")
        return jsonify({'error': _('Session not found')}), 404

    # Get pagination parameters
    since = request.args.get('since')
    try:
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
    except (ValueError, TypeError):
        limit = 100
        offset = 0
    
    # Enforce reasonable limits
    limit = min(limit, 200)  # Max 200 messages at once
    offset = max(offset, 0)  # No negative offset
    
    messages = db.get_session_messages(session_id, since=since, limit=limit, offset=offset)
    return jsonify({
        'messages': messages,
        'limit': limit,
        'offset': offset,
        'has_more': len(messages) >= limit
    })

@bp.route('/send_message', methods=['POST'])
def send_message():
    current_app.logger.info("=" * 50)
    current_app.logger.info("send_message: START PROCESSING")

    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401

    user_id = session['login']
    user_class = session.get('service_class', 2)
    session_id = session.get('current_session')

    if not session_id:
        session_id = db.create_session(user_id, lang=session.get('language', 'ru'))
        session['current_session'] = session_id

    message_text = ""
    file_data = None
    file_type = None
    file_name = None
    voice_record = False
    file_size_bytes = 0

    if request.content_type and 'multipart/form-data' in request.content_type:
        message_text = request.form.get('message', '')
        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename:
                file_bytes = file.read()
                file_size_bytes = len(file_bytes)
                file_data = base64.b64encode(file_bytes).decode('utf-8')
                file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
                file_name = file.filename
        voice_record = request.form.get('voice_record') == 'true'
    else:
        try:
            data = request.get_json()
            if data:
                message_text = data.get('message', '')
        except (json.JSONDecodeError, TypeError):
            # Fallback to form data if JSON parsing fails
            message_text = request.form.get('message', '')

    if not message_text and not file_data:
        return jsonify({'error': _('Empty message')}), 400

    request_type = 'text'
    if file_data and file_type:
        if file_type.startswith('image/'):
            request_type = 'image'
        elif current_app.modules['audio'].is_audio_file(file_type, file_name):
            request_type = 'audio'

    resize_notice = None
    file_path = None
    if request_type == 'image':
        max_width = current_app.config.get('MAX_IMAGE_WIDTH', 3840)
        max_height = current_app.config.get('MAX_IMAGE_HEIGHT', 2160)
        new_file_data, new_file_type, new_file_name, resized, orig_dims, new_dims = resize_image_if_needed(
            file_data, file_type, file_name, max_width, max_height
        )
        if resized:
            lang = session.get('language', 'ru')
            with force_locale(lang):
                resolution_msg = _('Maximum resolution {max_width}x{max_height}').format(
                    max_width=max_width, max_height=max_height
                )
                reduced_msg = _('The image has been reduced.')
                notice_text = f'⚠️ {resolution_msg}. {reduced_msg}'
                db.save_message(session_id, 'assistant', notice_text, model_name='system', response_time='0')
                resize_notice = notice_text
            file_data = new_file_data
            file_type = new_file_type
            file_name = new_file_name

        file_path = save_uploaded_file(
            file_data=file_data,
            filename=file_name,
            session_id=session_id,
            upload_folder=current_app.config['UPLOAD_FOLDER']
        )

    if request_type == 'audio':
        if voice_record:
            limit_mb = current_app.config['MAX_VOICE_SIZE_MB']
        else:
            limit_mb = current_app.config['MAX_AUDIO_SIZE_MB']
        if file_size_bytes > limit_mb * 1024 * 1024:
            return jsonify({'error': _('Maximum file size {max_size} MB').format(max_size=limit_mb)}), 400

    user_content = []
    if message_text:
        user_content.append({"type": "text", "text": message_text})
    if file_data:
        if file_type and file_type.startswith('image/'):
            content_type = "image"
        elif file_type and current_app.modules['audio'].is_audio_file(file_type, file_name):
            content_type = "audio"
        else:
            content_type = "file"
        user_content.append({"type": content_type, "file_data": file_data, "file_type": file_type, "file_name": file_name})

    user_content_json = json.dumps(user_content, ensure_ascii=False)
    user_message_id = db.save_message(session_id, 'user', user_content_json, file_data, file_type, file_name, None)

    with sqlite3.connect(db.CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
        message_count = c.fetchone()[0]
        is_first_message = message_count == 1
        if is_first_message:
            db.update_session_title(session_id, message_text, file_name)

    # Audio files are processed asynchronously: queue transcription task
    if request_type == 'audio':
        current_app.logger.info("send_message: audio detected, queueing transcription task")
        request_data = {
            'type': 'transcribe_audio',
            'file_data': file_data,
            'file_type': file_type,
            'file_name': file_name,
            'voice_record': voice_record,
            'preview': (message_text[:50] + '...') if message_text else (file_name or _('Voice request'))
        }
        request_id, position_info = current_app.request_queue.add_request(
            user_id, session_id, request_data, user_class,
            lang=session.get('language', 'ru')
        )
        response_data = {
            'status': 'queued',
            'request_id': request_id,
            'position': position_info['position'],
            'estimated_wait': position_info['estimated_seconds'],
            'message': _('Request queued (position {pos})').format(pos=position_info['position']),
            'user_message_id': user_message_id
        }
        if resize_notice:
            response_data['resize_notice'] = resize_notice
        return jsonify(response_data)

    # For text and image requests, queue the main processing task
    if request_type == 'image' and file_data:
        request_data = {
            'type': 'image',
            'text': message_text,
            'file_data': file_data,
            'file_type': file_type,
            'file_name': file_name,
            'preview': (message_text[:50] + '...') if message_text else (file_name or _('Image'))
        }
    else:
        request_data = {
            'type': 'text',
            'text': message_text,
            'preview': (message_text[:50] + '...') if message_text else _('Text request')
        }

    request_id, position_info = current_app.request_queue.add_request(
        user_id, session_id, request_data, user_class,
        lang=session.get('language', 'ru')
    )

    response_data = {
        'status': 'queued',
        'request_id': request_id,
        'position': position_info['position'],
        'estimated_wait': position_info['estimated_seconds'],
        'message': _('Request queued (position {pos})').format(pos=position_info['position']),
        'user_message_id': user_message_id
    }
    if resize_notice:
        response_data['resize_notice'] = resize_notice
    return jsonify(response_data)