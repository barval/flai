# app/routes/documents.py
import os
import uuid
import mimetypes
from flask import Blueprint, request, session, jsonify, current_app, send_file
from flask_babel import gettext as _
from app import db

bp = Blueprint('documents', __name__, url_prefix='/api')

@bp.route('/documents', methods=['GET'])
def api_get_documents():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    return jsonify(db.get_user_documents(session['login']))

@bp.route('/documents/upload', methods=['POST'])
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

    max_size_mb = current_app.config['MAX_DOCUMENT_SIZE_MB']
    if file_size > max_size_mb * 1024 * 1024:
        return jsonify({'error': _('Maximum file size {max_size} MB').format(max_size=max_size_mb)}), 400

    doc_id = str(uuid.uuid4())
    filename = file.filename

    documents_folder = current_app.config['DOCUMENTS_FOLDER']
    user_folder = os.path.join(documents_folder, session['login'])
    os.makedirs(user_folder, exist_ok=True)

    safe_filename = f"{doc_id}_{filename}"
    file_path = os.path.join(user_folder, safe_filename)
    with open(file_path, 'wb') as f:
        f.write(file_content)

    relative_path = os.path.join(session['login'], safe_filename)
    db.save_document(
        session['login'],
        doc_id,
        filename,
        file_size,
        ext,
        relative_path
    )

    db.update_document_index_status(doc_id, db.INDEX_STATUS_PENDING)

    current_app.request_queue.add_index_task(
        user_id=session['login'],
        doc_id=doc_id,
        file_path=file_path,
        lang=session.get('language', 'ru')
    )

    return jsonify({'status': 'ok', 'id': doc_id})

@bp.route('/documents/<doc_id>', methods=['GET'])
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

@bp.route('/documents/<doc_id>', methods=['DELETE'])
def api_delete_document(doc_id):
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401
    doc = db.get_document(doc_id, session['login'])
    if not doc:
        return jsonify({'error': _('Document not found')}), 404

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