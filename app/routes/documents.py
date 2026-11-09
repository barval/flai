# app/routes/documents.py
import hashlib
import io
import mimetypes
import os
import re
import subprocess
import tempfile
import uuid
import zipfile

import magic
from flask import Blueprint, Response, current_app, jsonify, request, send_file, session
from flask_babel import gettext as _

from app import db

bp = Blueprint("documents", __name__, url_prefix="/api")

# Mobile browsers (Chrome for Android in particular) cannot render a PDF in an
# <iframe> and show «This content is blocked» instead. The PDF preview therefore
# renders pages to JPEG server-side for narrow screens; this is the page cap.
PREVIEW_PDF_MAX_PAGES = 30
PREVIEW_PDF_SCALE = 1400

# One MIME type may back several extensions: text formats all sniff as
# text/plain, and the legacy .xls workbook shows up as a generic OLE container.
# Office/OpenDocument files are ZIP packages. libmagic can only sniff the first
# bytes it is given, and the marker that identifies an OOXML file sits either just
# past that window or in the ZIP directory at the end — so the same .xlsx is
# recognised on one libmagic build and reported as a plain archive on another.
# When magic has no opinion, the container is inspected instead: the extension
# decides which entry must exist inside the archive.
ZIP_PACKAGE_MARKERS = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
    ".pptx": "ppt/presentation.xml",
    ".odt": "content.xml",
    ".epub": "META-INF/container.xml",
}

ALLOWED_MIME_TYPES = {
    "application/pdf": {".pdf"},
    "application/msword": {".doc"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "text/plain": {".txt", ".md", ".markdown", ".log", ".ini", ".cfg", ".yaml", ".yml", ".toml", ".eml"},
    "application/vnd.oasis.opendocument.text": {".odt"},
    "application/rtf": {".rtf"},
    "text/rtf": {".rtf"},
    "text/csv": {".csv"},
    "application/json": {".json"},
    "application/epub+zip": {".epub"},
    "text/html": {".html", ".htm"},
    "application/xhtml+xml": {".html", ".htm"},
    "text/xml": {".xml", ".fb2"},
    "application/xml": {".xml", ".fb2"},
    "application/x-fictionbook+xml": {".fb2"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
    "application/vnd.ms-excel": {".xls"},
    "application/x-ole-storage": {".xls"},
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": {".pptx"},
    "message/rfc822": {".eml"},
    # Images — same formats as chat uploads (auto-resize + conversion to JPEG).
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/bmp": {".bmp"},
    "image/webp": {".webp"},
    "image/tiff": {".tiff", ".tif"},
    "image/gif": {".gif"},
    "image/heic": {".heic"},
}


def _validate_doc(file_stream) -> tuple[bool, str | None]:
    """Decide a .doc by its own container.

    libmagic reads the first two kilobytes and reports the generic OLE2 type for
    most Word documents — the marker sits further in — and that answer is the one
    a legacy .xls produces too, so the extension cannot be checked against it.
    The container is asked instead: a `WordDocument` stream with the Word
    identifier makes it a document, and a password-protected one is refused here
    because a stored-but-unreadable file would fail to index a second later.
    """
    from app.doc_extract import is_encrypted_doc, is_word_document

    file_stream.seek(0)
    payload = file_stream.read()
    file_stream.seek(0)
    if not is_word_document(payload):
        return False, _("File type does not match extension")
    if is_encrypted_doc(payload):
        return False, _("This .doc is password-protected. Remove the password and upload it again.")
    return True, None


def _is_zip_package(stream, ext: str) -> bool:
    """True when the stream is a ZIP archive carrying the format's own entry.

    A renamed archive fails: the extension decides which member must be present,
    so `.zip` renamed to `.xlsx` is refused while the real package is accepted
    whatever libmagic thinks of its first two kilobytes.
    """
    marker = ZIP_PACKAGE_MARKERS.get(ext)
    if not marker:
        return False
    try:
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            return marker in archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return False
    finally:
        stream.seek(0)


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
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".xlsx",
        ".xls",
        ".pptx",
        ".fb2",
        ".xml",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".log",
        ".eml",
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

    if ext == ".doc":
        return _validate_doc(file_stream)

    # Check magic bytes
    file_stream.seek(0)
    mime = magic.from_buffer(file_stream.read(2048), mime=True)
    file_stream.seek(0)

    if mime not in ALLOWED_MIME_TYPES:
        if _is_zip_package(file_stream, ext):
            return True, None
        return False, _("Unsupported file type")

    # Images: chef does auto-resize + format conversion (whatever the source
    # format — HEIC/BMP/TIFF/WebP are all converted to JPEG on upload), so a
    # strict ext↔MIME match is intentionally skipped for image/* (same policy
    # as chat uploads, where magic bytes win over the declared extension).
    if mime.startswith("image/"):
        return True, None

    # Verify extension matches MIME type
    if ext not in ALLOWED_MIME_TYPES[mime]:
        return False, _("File type does not match extension")

    return True, None


@bp.route("/documents", methods=["GET"])
def api_get_documents():
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    login = session["login"]
    documents = db.get_user_documents(login, include_shared=True, with_share_counts=True)
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
    max_folders = current_app.config.get("MAX_DOCUMENTS_PER_USER", 500)
    if len(db.get_user_folders(login)) >= max_folders:
        return jsonify({"error": _("Folder limit reached ({n})").format(n=max_folders)}), 409
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
        db.delete_document_resources(doc, login)
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
    # One physical file per content, shared by every user who uploads it. The
    # per-user record stays independent: deleting it drops a reference and the
    # file survives while anybody else still holds the same content.
    blobs_folder = os.path.join(documents_folder, "_blobs")
    os.makedirs(blobs_folder, exist_ok=True)

    sha256 = hashlib.sha256(file_content).hexdigest()
    safe_ext = os.path.splitext(filename)[1].lower()
    relative_path = os.path.join("_blobs", f"{sha256}{safe_ext}")
    file_path = os.path.join(documents_folder, relative_path)

    # The blob path is built from a hex digest and a lowercased extension, never
    # from user input; the containment check stays as a guard against a future
    # change to that construction.
    real_file_path = os.path.realpath(file_path)
    real_blobs_folder = os.path.realpath(blobs_folder)
    if not real_file_path.startswith(real_blobs_folder + os.sep):
        return jsonify({"error": _("Invalid file path")}), 400

    created_blob = db.retain_document_blob(sha256, relative_path, file_size, image_mime or None)
    if created_blob:
        with open(file_path, "wb") as f:
            f.write(file_content)
    else:
        # Reuse the stored copy: the extension of this upload may differ, so the
        # authoritative path comes from the blob row.
        with db.get_db() as _conn:
            _c = _conn.cursor()
            _c.execute("SELECT file_path FROM document_blobs WHERE sha256 = %s", (sha256,))
            _row = _c.fetchone()
        if _row and _row["file_path"]:
            relative_path = _row["file_path"]
            file_path = os.path.join(documents_folder, relative_path)
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
        blob_sha256=sha256,
        stored_bytes=file_size if created_blob else 0,
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
    doc = db.get_readable_document(doc_id, session["login"])
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


PREVIEW_MAX_CHARS = 200_000
_CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)", re.I)
_PREVIEW_TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".ini", ".cfg", ".yaml", ".yml", ".toml", ".xml"}
_PREVIEW_EXTRACTED_EXTS = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".epub", ".html", ".htm", ".xlsx", ".xls", ".pptx", ".fb2", ".eml"}
_PREVIEW_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".gif"}


def _html_charset(body: bytes) -> str:
    """The encoding the page declares, so a cp1251 report is not mojibake.

    Browsers honour the document's own meta tag; the header is what they read
    before parsing, and a wrong one there would be unrecoverable.
    """
    match = _CHARSET_RE.search(body[:4096])
    if not match:
        return "utf-8"
    name = match.group(1).decode("ascii", "ignore").lower()
    try:
        "".encode(name)
    except LookupError:
        return "utf-8"
    return name


def _owned_document_file(doc_id: str):
    """Resolve an owned document to a safe on-disk path (or an error response)."""
    if "login" not in session:
        return None, None, (jsonify({"error": _("Not authorized")}), 401)
    doc = db.get_readable_document(doc_id, session["login"])
    if not doc:
        return None, None, (jsonify({"error": _("Document not found")}), 404)
    documents_folder = current_app.config["DOCUMENTS_FOLDER"]
    file_path = os.path.join(documents_folder, doc["file_path"])
    real_file_path = os.path.realpath(file_path)
    real_documents_folder = os.path.realpath(documents_folder)
    if not real_file_path.startswith(real_documents_folder + os.sep) and real_file_path != real_documents_folder:
        current_app.logger.warning(f"Path traversal attempt blocked: {doc['file_path']}")
        return None, None, (jsonify({"error": _("Permission denied")}), 403)
    if not os.path.exists(file_path):
        current_app.logger.error(f"Document file not found: {file_path}")
        return None, None, (jsonify({"error": _("File not found")}), 404)
    return doc, file_path, None


def _pdf_page_count(file_path: str) -> int:
    """Page count from ``pdfinfo``; 0 when the file cannot be read."""
    try:
        result = subprocess.run(["pdfinfo", file_path], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        current_app.logger.warning(f"PDF page count failed ({file_path}): {e}")
        return 0
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, re.MULTILINE)
    if result.returncode != 0 or not match:
        return 0
    return int(match.group(1))


@bp.route("/documents/<doc_id>/preview", methods=["GET"])
def api_document_preview(doc_id):
    """Preview metadata/text for the Documents panel.

    PDF and images are returned as a URL served inline by
    ``/documents/<id>/preview/file``; office formats are converted to text
    (``extracted: true``); plain text is returned as is. PDFs additionally
    report their page count so the UI can render page images on mobile.
    """
    doc, file_path, error = _owned_document_file(doc_id)
    if error:
        return error
    ext = os.path.splitext(doc["file_path"])[1].lower()
    payload = {"filename": doc["filename"], "ext": ext, "extracted": False, "truncated": False}
    if ext in _PREVIEW_IMAGE_EXTS:
        payload.update({"kind": "binary", "mime": mimetypes.guess_type(file_path)[0] or "image/png",
                        "url": f"/api/documents/{doc_id}/preview/file"})
        return jsonify(payload)
    if ext == ".pdf":
        payload.update({
            "kind": "pdf",
            "mime": "application/pdf",
            "pages": _pdf_page_count(file_path),
            "max_pages": PREVIEW_PDF_MAX_PAGES,
            "url": f"/api/documents/{doc_id}/preview/file",
            "page_url": f"/api/documents/{doc_id}/preview/pdf-page/",
        })
        return jsonify(payload)
    if ext in _PREVIEW_TEXT_EXTS:
        try:
            with open(file_path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(PREVIEW_MAX_CHARS + 1)
        except OSError as e:
            current_app.logger.warning(f"Document preview read failed ({doc_id}): {e}")
            return jsonify({"error": _("Could not read the document")}), 500
        truncated = len(text) > PREVIEW_MAX_CHARS
        payload.update({"kind": "text", "text": text[:PREVIEW_MAX_CHARS], "truncated": truncated})
        return jsonify(payload)
    if ext in (".html", ".htm"):
        payload.update({"kind": "html", "url": f"/api/documents/{doc_id}/preview/html"})
        return jsonify(payload)
    if ext in _PREVIEW_EXTRACTED_EXTS and ext != ".pdf":
        from app.utils import extract_text_from_file

        text = extract_text_from_file(file_path) or ""
        truncated = len(text) > PREVIEW_MAX_CHARS
        if not text.strip():
            payload.update({"kind": "unsupported"})
        else:
            payload.update({"kind": "text", "text": text[:PREVIEW_MAX_CHARS],
                            "extracted": True, "truncated": truncated})
        return jsonify(payload)
    payload.update({"kind": "unsupported"})
    return jsonify(payload)


@bp.route("/documents/<doc_id>/preview/file", methods=["GET"])
def api_document_preview_file(doc_id):
    """Serve a PDF/image inline for the preview iframe (never HTML)."""
    doc, file_path, error = _owned_document_file(doc_id)
    if error:
        return error
    ext = os.path.splitext(doc["file_path"])[1].lower()
    if ext == ".pdf":
        mimetype = "application/pdf"
    elif ext in _PREVIEW_IMAGE_EXTS:
        mimetype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    else:
        return jsonify({"error": _("Preview is not available for this format")}), 415
    response = send_file(file_path, mimetype=mimetype, as_attachment=False, download_name=doc["filename"])
    # The global after_request sets frame-ancestors 'none' + X-Frame-Options:
    # DENY, which would block the same-origin preview iframe. The marker keeps
    # those headers off this response only.
    response.headers["X-Own-CSP"] = "1"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response


@bp.route("/documents/<doc_id>/preview/html", methods=["GET"])
def api_document_preview_html(doc_id):
    """Render an uploaded page inside the preview iframe, sandboxed.

    Serving user HTML is an XSS vector: a script in the file would run in the
    panel's origin with the reader's session. The document therefore arrives with
    a `sandbox` CSP directive — an opaque origin, no scripts, no forms, no
    navigation — and `default-src 'none'`, so nothing is fetched from anywhere.
    Inline styles still apply, which is what makes an exported report readable; a
    page that links a sibling stylesheet stays plain, because those files are not
    served.
    """
    doc, file_path, error = _owned_document_file(doc_id)
    if error:
        return error
    ext = os.path.splitext(doc["file_path"])[1].lower()
    if ext not in (".html", ".htm"):
        return jsonify({"error": _("Preview is not available for this format")}), 415
    try:
        with open(file_path, "rb") as handle:
            body = handle.read()
    except OSError as e:
        current_app.logger.warning(f"Document preview read failed ({doc_id}): {e}")
        return jsonify({"error": _("Could not read the document")}), 500
    response = Response(body, mimetype="text/html")
    response.headers["Content-Type"] = f"text/html; charset={_html_charset(body)}"
    response.headers["X-Own-CSP"] = "1"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
        "frame-ancestors 'self'"
    )
    return response


@bp.route("/documents/<doc_id>/preview/pdf-page/<int:page>", methods=["GET"])
def api_document_preview_pdf_page(doc_id, page):
    """Render one PDF page as JPEG for the mobile preview (no PDF plugin).

    Chrome for Android cannot display a PDF inside an iframe; page images give
    the same read-only preview on a phone without a download or an external
    viewer. Only the first ``PREVIEW_PDF_MAX_PAGES`` pages are available.
    """
    doc, file_path, error = _owned_document_file(doc_id)
    if error:
        return error
    ext = os.path.splitext(doc["file_path"])[1].lower()
    if ext != ".pdf":
        return jsonify({"error": _("Preview is not available for this format")}), 415
    if page < 1 or page > PREVIEW_PDF_MAX_PAGES:
        return jsonify({"error": _("Page not found")}), 404
    pages = _pdf_page_count(file_path)
    if not pages or page > pages:
        return jsonify({"error": _("Page not found")}), 404
    try:
        with tempfile.TemporaryDirectory(prefix="flai-pdf-preview-") as temp_dir:
            prefix = os.path.join(temp_dir, "page")
            result = subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    "-jpeg",
                    "-scale-to",
                    str(PREVIEW_PDF_SCALE),
                    file_path,
                    prefix,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            page_path = f"{prefix}.jpg"
            if result.returncode != 0 or not os.path.exists(page_path):
                detail = result.stderr.strip() or f"failed to render page {page}"
                current_app.logger.warning(f"PDF preview page rendering failed ({doc_id}): {detail}")
                return jsonify({"error": _("Could not render the page")}), 500
            with open(page_path, "rb") as fh:
                data = fh.read()
    except (OSError, subprocess.SubprocessError) as e:
        current_app.logger.warning(f"PDF preview page rendering failed ({doc_id}): {e}")
        return jsonify({"error": _("Could not render the page")}), 500
    response = send_file(
        io.BytesIO(data),
        mimetype="image/jpeg",
        as_attachment=False,
        download_name=f"{os.path.splitext(doc['filename'])[0]}-{page}.jpg",
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@bp.route("/documents/shareable-users", methods=["GET"])
def api_shareable_users():
    """Logins a document may be shared with.

    The caller is not offered their own document, and neither is the admin:
    the admin account has no chats and no documents, so a share to it would be
    a permission nobody can use.
    """
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    from app.userdb import get_all_user_ids

    login = session["login"]
    users = [other for other in get_all_user_ids() if other != login and other != "admin"]
    return jsonify({"users": users})


@bp.route("/documents/<doc_id>/shares", methods=["GET"])
def api_get_document_shares(doc_id):
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    if not db.get_document(doc_id, session["login"]):
        return jsonify({"error": _("Document not found")}), 404
    return jsonify({"shares": db.get_document_shares(doc_id)})


@bp.route("/documents/<doc_id>/share", methods=["POST"])
def api_share_document(doc_id):
    """Give another user read access; only the owner may do this."""
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    login = session["login"]
    if not db.get_document(doc_id, login):
        return jsonify({"error": _("Document not found")}), 404

    from app.userdb import get_user_by_login

    payload = request.get_json(silent=True) or {}
    shared_with = str(payload.get("login") or "").strip()
    if not shared_with:
        return jsonify({"error": _("Choose a user to share with")}), 400
    if shared_with == login:
        return jsonify({"error": _("You already have access to this document")}), 400
    if not get_user_by_login(shared_with):
        return jsonify({"error": _("User not found")}), 404

    db.share_document(doc_id, login, shared_with)
    return jsonify({"status": "ok", "shares": db.get_document_shares(doc_id)})


@bp.route("/documents/<doc_id>/share/<shared_with>", methods=["DELETE"])
def api_unshare_document(doc_id, shared_with):
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    if not db.get_document(doc_id, session["login"]):
        return jsonify({"error": _("Document not found")}), 404
    db.unshare_document(doc_id, shared_with)
    return jsonify({"status": "ok", "shares": db.get_document_shares(doc_id)})


@bp.route("/documents/<doc_id>", methods=["DELETE"])
def api_delete_document(doc_id):
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    doc = db.get_document(doc_id, session["login"])
    if not doc:
        return jsonify({"error": _("Document not found")}), 404

    # One deletion path for every caller: it releases the blob reference and
    # unlinks the file only when this was the last document using it. This route
    # used to unlink the file itself, which removed a shared blob out from under
    # every other document pointing at it.
    db.delete_document_resources(doc, session["login"])
    return jsonify({"status": "ok"})
