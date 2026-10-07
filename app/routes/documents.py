# app/routes/documents.py
import contextlib
import mimetypes
import os
import uuid

import magic
from flask import Blueprint, current_app, jsonify, request, send_file, session
from flask_babel import gettext as _

from app import db

bp = Blueprint("documents", __name__, url_prefix="/api")

# Mapping of MIME types to allowed extensions
ALLOWED_MIME_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/epub+zip": ".epub",
    # Images — same formats as chat uploads (auto-resize + conversion to JPEG).
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/gif": ".gif",
    "image/heic": ".heic",
}


def validate_file(file_stream, filename):
    """Validate file by extension and magic bytes.
    Returns (is_valid, error_message).
    """
    allowed_extensions = {
        ".pdf",
        ".doc",
        ".docx",
        ".txt",
        ".odt",
        ".rtf",
        ".csv",
        ".json",
        ".epub",
        # Images — same set as chat uploads (auto-resize + conversion in upload).
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".webp",
        ".tiff",
        ".tif",
        ".gif",
        ".heic",
    }
    ext = os.path.splitext(filename)[1].lower()

    if ext not in allowed_extensions:
        return False, _("Unsupported file type")

    # Check magic bytes
    file_stream.seek(0)
    mime = magic.from_buffer(file_stream.read(2048), mime=True)
    file_stream.seek(0)

    if mime not in ALLOWED_MIME_TYPES:
        return False, _("Unsupported file type")

    # Images: chef does auto-resize + format conversion (whatever the source
    # format — HEIC/BMP/TIFF/WebP are all converted to JPEG on upload), so a
    # strict ext↔MIME match is intentionally skipped for image/* (same policy
    # as chat uploads, where magic bytes win over the declared extension).
    if mime.startswith("image/"):
        return True, None

    # Verify extension matches MIME type
    expected_ext = ALLOWED_MIME_TYPES[mime]
    if ext != expected_ext:
        return False, _("File type does not match extension")

    return True, None


@bp.route("/documents", methods=["GET"])
def api_get_documents():
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    login = session["login"]
    documents = db.get_user_documents(login)
    folders = db.get_user_folders(login)
    counts = {f["id"]: 0 for f in folders}
    sizes = {f["id"]: 0 for f in folders}
    for doc in documents:
        fid = doc.get("folder_id")
        if fid and fid in counts:
            counts[fid] += 1
            sizes[fid] += doc.get("file_size") or 0
    payload_folders = [
        {
            "id": f["id"],
            "name": f["name"],
            "created_at": f["created_at"],
            "count": counts[f["id"]],
            "size": sizes[f["id"]],
        }
        for f in folders
    ]
    return jsonify({"folders": payload_folders, "documents": documents})


def _validate_folder_name(name):
    """Return (normalized_name, error_message) for a folder name."""
    name = (name or "").strip()
    if not name:
        return None, _("Folder name is required")
    if len(name) > 64:
        return None, _("Folder name is too long")
    return name, None


def _is_duplicate_folder(login, name, exclude_id=None):
    for f in db.get_user_folders(login):
        if f["id"] == exclude_id:
            continue
        if f["name"].casefold() == name.casefold():
            return True
    return False


@bp.route("/document-folders", methods=["POST"])
def api_create_folder():
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    login = session["login"]
    name, error = _validate_folder_name((request.json or {}).get("name"))
    if error:
        return jsonify({"error": error}), 400
    if _is_duplicate_folder(login, name):
        return jsonify({"error": _("A folder with this name already exists")}), 409
    folder_id = str(uuid.uuid4())
    db.save_folder(login, folder_id, name)
    return jsonify({"status": "ok", "id": folder_id})


@bp.route("/document-folders/<folder_id>", methods=["PATCH"])
def api_rename_folder(folder_id):
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    login = session["login"]
    if not db.get_folder(folder_id, login):
        return jsonify({"error": _("Folder not found")}), 404
    name, error = _validate_folder_name((request.json or {}).get("name"))
    if error:
        return jsonify({"error": error}), 400
    if _is_duplicate_folder(login, name, exclude_id=folder_id):
        return jsonify({"error": _("A folder with this name already exists")}), 409
    db.rename_folder(folder_id, login, name)
    return jsonify({"status": "ok"})


def _delete_document_resources(doc, login):
    """Delete one document: RAG entry, file on disk and DB row."""
    rag = current_app.modules.get("rag")
    if rag and rag.available:
        try:
            rag.delete_document(doc["id"], login)
        except Exception as e:
            current_app.logger.error(f"Failed to delete document from index: {e}")
    documents_folder = current_app.config["DOCUMENTS_FOLDER"]
    file_path = os.path.join(documents_folder, doc.get("file_path") or "")
    real_file_path = os.path.realpath(file_path)
    real_documents_folder = os.path.realpath(documents_folder)
    if real_file_path.startswith(real_documents_folder + os.sep) and os.path.exists(file_path):
        with contextlib.suppress(Exception):
            os.remove(file_path)
    db.delete_document(doc["id"], login)


@bp.route("/document-folders/<folder_id>", methods=["DELETE"])
def api_delete_folder(folder_id):
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    login = session["login"]
    if not db.get_folder(folder_id, login):
        return jsonify({"error": _("Folder not found")}), 404
    docs = db.get_folder_documents(login, folder_id)
    deleted_size = sum(doc.get("file_size") or 0 for doc in docs)
    for doc in docs:
        _delete_document_resources(doc, login)
    db.delete_folder(folder_id, login)
    return jsonify({"status": "ok", "deleted_documents": len(docs), "deleted_size": deleted_size})


@bp.route("/documents/<doc_id>/folder", methods=["PATCH"])
def api_set_document_folder(doc_id):
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    login = session["login"]
    if not db.get_document(doc_id, login):
        return jsonify({"error": _("Document not found")}), 404
    data = request.json or {}
    if "folder_id" not in data:
        return jsonify({"error": _("Missing folder_id")}), 400
    folder_id = data["folder_id"]
    if folder_id is not None and not db.get_folder(folder_id, login):
        return jsonify({"error": _("Folder not found")}), 404
    db.set_document_folder(doc_id, login, folder_id)
    return jsonify({"status": "ok"})


@bp.route("/documents/move", methods=["POST"])
def api_move_documents():
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    login = session["login"]
    data = request.json or {}
    doc_ids = data.get("doc_ids")
    if not isinstance(doc_ids, list) or not doc_ids:
        return jsonify({"error": _("No documents selected")}), 400
    folder_id = data.get("folder_id")
    if folder_id is not None and not db.get_folder(folder_id, login):
        return jsonify({"error": _("Folder not found")}), 404
    moved = sum(1 for did in doc_ids if db.set_document_folder(did, login, folder_id))
    return jsonify({"status": "ok", "moved": moved})


@bp.route("/documents/upload", methods=["POST"])
def api_upload_document():
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    if "file" not in request.files:
        return jsonify({"error": _("No file provided")}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": _("No file selected")}), 400

    # Read file content
    file_content = file.read()
    file_size = len(file_content)

    # Validate file type
    from io import BytesIO

    file_stream = BytesIO(file_content)
    is_valid, error_message = validate_file(file_stream, file.filename)
    if not is_valid:
        return jsonify({"error": error_message}), 400

    # Check file size
    max_size_mb = current_app.config["MAX_DOCUMENT_SIZE_MB"]
    if file_size > max_size_mb * 1024 * 1024:
        return jsonify({"error": _("Maximum file size {max_size} MB").format(max_size=max_size_mb)}), 400

    # Check document quota
    from app.utils import check_document_quota

    quota_error = check_document_quota(session["login"])
    if quota_error:
        return jsonify({"error": quota_error}), 413

    doc_id = str(uuid.uuid4())
    filename = file.filename

    # Track if this is an image to enqueue the right task type
    is_image = False

    # Images — same auto-resize + conversion pipeline as chat uploads, so the
    # stored document is always a normalized JPEG (HEIC/BMP/TIFF/WebP/GIF and
    # oversized images are converted/resized the same way the chat does).
    image_mime = magic.from_buffer(file_content[:2048], mime=True) if file_content[:2048] else None
    if image_mime and image_mime.startswith("image/"):
        is_image = True
        import base64

        from app.utils import convert_to_supported_format_if_needed, resize_image_if_needed

        max_image_size = current_app.config.get("MAX_IMAGE_SIZE", 1536)
        file_b64 = base64.b64encode(file_content).decode("ascii")
        file_b64, _ftype, img_orig_name, _resized, _od, _nd = resize_image_if_needed(
            file_b64, image_mime, filename, max_image_size
        )
        file_b64, _ftype, img_new_name, _converted = convert_to_supported_format_if_needed(
            file_b64, _ftype, _ftype and filename or filename
        )
        file_content = base64.b64decode(file_b64)
        file_size = len(file_content)
        filename = img_new_name

    documents_folder = current_app.config["DOCUMENTS_FOLDER"]
    user_folder = os.path.join(documents_folder, session["login"])
    os.makedirs(user_folder, exist_ok=True)

    # Safe name: UUID + original extension
    safe_ext = os.path.splitext(filename)[1].lower()
    safe_filename = f"{doc_id}{safe_ext}"
    file_path = os.path.join(user_folder, safe_filename)

    # Double-check path does not escape DOCUMENTS_FOLDER
    real_file_path = os.path.realpath(file_path)
    real_user_folder = os.path.realpath(user_folder)
    if not real_file_path.startswith(real_user_folder + os.sep):
        return jsonify({"error": _("Invalid file path")}), 400

    with open(file_path, "wb") as f:
        f.write(file_content)

    relative_path = os.path.join(session["login"], safe_filename)
    folder_id = request.form.get("folder_id") or None
    if folder_id is not None and not db.get_folder(folder_id, session["login"]):
        return jsonify({"error": _("Folder not found")}), 400
    db.save_document(
        session["login"],
        doc_id,
        filename,
        file_size,
        file_ext=os.path.splitext(filename)[1].lower(),
        file_path=relative_path,
        folder_id=folder_id,
    )

    db.update_document_index_status(doc_id, db.INDEX_STATUS_PENDING)

    # Add indexing task to the queue
    task_type = "describe_document_image" if is_image else "index_document"
    current_app.request_queue.add_request(
        user_id=session["login"],
        session_id="",  # Document indexing doesn't belong to a chat session
        request_data={
            "type": task_type,
            "doc_id": doc_id,
            "file_path": file_path,
        },
        user_class=session.get("user_class", 100),
        lang=session.get("language", "ru"),
    )

    return jsonify({"status": "ok", "id": doc_id})


@bp.route("/documents/<doc_id>", methods=["GET"])
def api_get_document(doc_id):
    from flask_babel import gettext as _

    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    doc = db.get_document(doc_id, session["login"])
    if not doc:
        return jsonify({"error": _("Document not found")}), 404

    documents_folder = current_app.config["DOCUMENTS_FOLDER"]
    file_path = os.path.join(documents_folder, doc["file_path"])

    # Security: prevent path traversal attacks
    # Normalize the path and verify it's still within documents_folder
    real_file_path = os.path.realpath(file_path)
    real_documents_folder = os.path.realpath(documents_folder)
    if not real_file_path.startswith(real_documents_folder + os.sep) and real_file_path != real_documents_folder:
        current_app.logger.warning(f"Path traversal attempt blocked: {doc['file_path']}")
        return jsonify({"error": _("Permission denied")}), 403

    if not os.path.exists(file_path):
        current_app.logger.error(f"Document file not found: {file_path}")
        return jsonify({"error": _("File not found")}), 404

    mimetype, _ = mimetypes.guess_type(file_path)
    if not mimetype:
        mimetype = "application/octet-stream"

    return send_file(file_path, mimetype=mimetype, as_attachment=True, download_name=doc["filename"])


@bp.route("/documents/<doc_id>", methods=["DELETE"])
def api_delete_document(doc_id):
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    doc = db.get_document(doc_id, session["login"])
    if not doc:
        return jsonify({"error": _("Document not found")}), 404

    rag = current_app.modules.get("rag")
    if rag and rag.available:
        try:
            rag.delete_document(doc_id, session["login"])
        except Exception as e:
            current_app.logger.error(f"Failed to delete document from index: {e}")

    documents_folder = current_app.config["DOCUMENTS_FOLDER"]
    file_path = os.path.join(documents_folder, doc["file_path"])

    # Security: prevent path traversal attacks
    real_file_path = os.path.realpath(file_path)
    real_documents_folder = os.path.realpath(documents_folder)
    if not real_file_path.startswith(real_documents_folder + os.sep) and real_file_path != real_documents_folder:
        current_app.logger.warning(f"Path traversal attempt blocked: {doc['file_path']}")
        return jsonify({"error": _("Permission denied")}), 403

    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            current_app.logger.error(f"Error deleting file {file_path}: {e}")

    db.delete_document(doc_id, session["login"])
    return jsonify({"status": "ok"})
