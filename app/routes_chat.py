# app/routes_chat.py
# Chat routes - handles session management, message sending, and document uploads

import sqlite3
import json
import base64
import time
import mimetypes
import os
import uuid
from datetime import datetime
from flask import Blueprint, render_template, request, session, jsonify, current_app, redirect, url_for, send_file
from flask_babel import gettext as _, gettext, force_locale
from . import db
from .utils import get_current_time_in_timezone, get_current_time_in_timezone_for_db, format_prompt, resize_image_if_needed, save_uploaded_file

bp = Blueprint('chat', __name__)


@bp.route('/')
def index():
    if 'login' not in session:
        return redirect(url_for('auth.login'))
    return redirect(url_for('chat.chat'))


@bp.route('/chat')
def chat():
    if 'login' not in session:
        return redirect(url_for('auth.login'))
    if session.get('is_admin'):
        return redirect(url_for('admin.admin_panel'))
    
    user_id = session['login']
    sessions = db.get_user_sessions(user_id)
    documents = db.get_user_documents(user_id)
    
    if not session.get('current_session'):
        last_id = db.get_last_session(user_id)
        if last_id and any(s['id'] == last_id for s in sessions):
            session['current_session'] = last_id
        elif sessions:
            session['current_session'] = sessions[0]['id']
        else:
            new_id = db.create_session(user_id, lang=session.get('language', 'ru'))
            session['current_session'] = new_id
            sessions = db.get_user_sessions(user_id)
    
    return render_template('chat.html',
                          sessions=sessions,
                          documents=documents,
                          current_session=session.get('current_session'))


@bp.route('/api/sessions', methods=['GET'])
def api_get_sessions():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    return jsonify(db.get_user_sessions(session['login']))


@bp.route('/api/sessions/<session_id>/messages', methods=['GET'])
def api_get_messages(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    since = request.args.get('since')
    return jsonify(db.get_session_messages(session_id, since=since))


@bp.route('/api/sessions/<session_id>/switch', methods=['POST'])
def api_switch_session(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    user_id = session['login']
    session['current_session'] = session_id
    db.set_last_session(user_id, session_id)
    db.update_session_visit(user_id, session_id)
    return jsonify({'status': 'ok'})


@bp.route('/api/sessions/<session_id>/model-info', methods=['GET'])
def api_get_session_model(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    with sqlite3.connect(db.CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT model_name FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        return jsonify({'model_name': row[0] if row else 'auto'})


@bp.route('/api/sessions/new', methods=['POST'])
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


@bp.route('/api/sessions/<session_id>/update-title', methods=['POST'])
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


@bp.route('/api/sessions/<session_id>/delete', methods=['POST'])
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


@bp.route('/api/sessions/<session_id>/visit', methods=['POST'])
def api_update_session_visit(session_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    db.update_session_visit(session['login'], session_id)
    return jsonify({'status': 'ok'})


@bp.route('/api/documents', methods=['GET'])
def api_get_documents():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    return jsonify(db.get_user_documents(session['login']))


@bp.route('/api/documents/upload', methods=['POST'])
def api_upload_document():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    if 'file' not in request.files:
        return jsonify({'error': _('No file provided')}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': _('No file selected')}), 400
    
    allowed_extensions = {'.pdf', '.doc', '.docx', '.txt'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        return jsonify({'error': _('Unsupported file type')}), 400
    
    file_content = file.read()
    file_size = len(file_content)
    
    # Check file size limit
    max_size_mb = current_app.config['MAX_DOCUMENT_SIZE_MB']
    if file_size > max_size_mb * 1024 * 1024:
        return jsonify({'error': _('Maximum file size {max_size} MB').format(max_size=max_size_mb)}), 400
    
    doc_id = str(uuid.uuid4())
    filename = file.filename
    
    documents_folder = current_app.config['DOCUMENTS_FOLDER']
    user_folder = os.path.join(documents_folder, session['login'])
    os.makedirs(user_folder, exist_ok=True)
    
    # Save file with unique name
    safe_filename = f"{doc_id}_{filename}"
    file_path = os.path.join(user_folder, safe_filename)
    with open(file_path, 'wb') as f:
        f.write(file_content)
    
    # Store relative path for database (relative to documents_folder)
    relative_path = os.path.join(session['login'], safe_filename)
    db.save_document(
        session['login'],
        doc_id,
        filename,
        file_size,
        ext,
        relative_path
    )
    
    # Set initial index status to pending
    db.update_document_index_status(doc_id, db.INDEX_STATUS_PENDING)
    
    # Add indexing task to queue (using absolute path for file access)
    current_app.request_queue.add_index_task(
        user_id=session['login'],
        doc_id=doc_id,
        file_path=file_path,  # absolute path for indexing
        lang=session.get('language', 'ru')
    )
    
    return jsonify({'status': 'ok', 'id': doc_id})


@bp.route('/api/documents/<doc_id>', methods=['GET'])
def api_get_document(doc_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    doc = db.get_document(doc_id, session['login'])
    if not doc:
        return jsonify({'error': _('Document not found')}), 404
    
    documents_folder = current_app.config['DOCUMENTS_FOLDER']
    file_path = os.path.join(documents_folder, doc['file_path'])
    if not os.path.exists(file_path):
        current_app.logger.error(f"Document file not found: {file_path}")
        return jsonify({'error': _('File not found')}), 404
    
    mimetype, _ = mimetypes.guess_type(file_path)
    if not mimetype:
        mimetype = 'application/octet-stream'
    
    return send_file(file_path, mimetype=mimetype, as_attachment=True, download_name=doc['filename'])


@bp.route('/api/documents/<doc_id>', methods=['DELETE'])
def api_delete_document(doc_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    doc = db.get_document(doc_id, session['login'])
    if not doc:
        return jsonify({'error': _('Document not found')}), 404
    
    # Delete from Qdrant index if needed
    rag = current_app.modules.get('rag')
    if rag and rag.available:
        try:
            rag.delete_document(doc_id, session['login'])
        except Exception as e:
            current_app.logger.error(f"Failed to delete document from index: {e}")
    
    documents_folder = current_app.config['DOCUMENTS_FOLDER']
    file_path = os.path.join(documents_folder, doc['file_path'])
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            current_app.logger.error(f"Error deleting file {file_path}: {e}")
    
    db.delete_document(doc_id, session['login'])
    return jsonify({'status': 'ok'})


@bp.route('/api/footer-text', methods=['GET'])
def api_footer_text():
    lang = session.get('language', 'ru')
    with force_locale(lang):
        return gettext('footer_text')


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
        except:
            message_text = request.form.get('message', '')
    
    if not message_text and not file_data:
        return jsonify({'error': _('Empty message')}), 400
    
    request_type = 'text'
    if file_data and file_type:
        if file_type.startswith('image/'):
            request_type = 'image'
        elif current_app.modules['audio'].is_audio_file(file_type, file_name):
            request_type = 'audio'
    
    # --- Image resize handling ---
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
    
    # --- Audio file size check ---
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
    
    if request_type == 'audio':
        current_app.logger.info("send_message: audio detected, starting transcription")
        transcribe_start = time.time()
        user_lang = session.get('language', 'ru')
        transcribed_text = current_app.modules['audio'].transcribe(
            file_data, file_type, file_name, lang=user_lang
        )
        transcribe_time = round(time.time() - transcribe_start, 1)
        
        if transcribed_text is None:
            lang = session.get('language', 'ru')
            with force_locale(lang):
                return jsonify({'error': _('Failed to recognize speech')}), 500
        
        current_app.logger.info(f"send_message: transcription successful in {transcribe_time}s")
        lang = session.get('language', 'ru')
        with force_locale(lang):
            system_content = '🎤 ' + _('Transcribed') + ': ' + transcribed_text
        transcribed_message_id = db.save_message(session_id, 'assistant', system_content, model_name='whisper', response_time=transcribe_time)
        
        if voice_record:
            current_app.logger.info("send_message: voice message, queueing task with transcribed text")
            request_data = {
                'type': 'text',
                'text': transcribed_text,
                'preview': (transcribed_text[:50] + '...') if transcribed_text else _('Voice request')
            }
            request_id, position_info = current_app.request_queue.add_request(
                user_id, session_id, request_data, user_class,
                lang=session.get('language', 'ru')
            )
            return jsonify({
                'status': 'queued',
                'transcribed_text': transcribed_text,
                'transcribed_message_id': transcribed_message_id,
                'user_message_id': user_message_id,
                'session_id': session_id,
                'request_id': request_id,
                'position': position_info['position'],
                'estimated_wait': position_info['estimated_seconds'],
                'response_time': transcribe_time,
                'message': _('Speech recognized, request queued (position {pos})').format(pos=position_info['position'])
            })
        else:
            return jsonify({
                'status': 'success',
                'transcribed_text': transcribed_text,
                'transcribed_message_id': transcribed_message_id,
                'user_message_id': user_message_id,
                'session_id': session_id,
                'response_time': transcribe_time,
                'message': _('Audio transcribed')
            })
    
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