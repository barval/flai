# app/db.py
# Database functions - PostgreSQL only
import contextlib
import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

from flask import current_app
from flask_babel import gettext as _

from app.database import get_db


def get_current_time_for_db():
    """Return the current time in DB format, taking timezone into account."""
    from .utils import get_current_time_in_timezone_for_db

    return get_current_time_in_timezone_for_db()


def get_user_storage_usage(user_id: str) -> int:
    """Get user's current upload storage usage in bytes. O(1) lookup."""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT used_bytes FROM user_storage WHERE user_id = %s", (user_id,))
            row = c.fetchone()
            return row["used_bytes"] if row else 0
    except Exception:
        return 0


def update_user_storage(user_id: str, delta_bytes: int) -> None:
    """Update user's storage counter. delta_bytes can be positive (upload) or negative (delete)."""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO user_storage (user_id, used_bytes)
                VALUES (%s, GREATEST(0, %s))
                ON CONFLICT(user_id) DO UPDATE SET used_bytes = GREATEST(0, user_storage.used_bytes + %s)
            """,
                (user_id, delta_bytes if delta_bytes > 0 else 0, delta_bytes),
            )
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"Failed to update user storage: {e}")


def get_user_sessions(user_id: str) -> list[dict[str, Any]]:
    """Get all sessions for a user."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        SELECT
            cs.id,
            cs.title,
            cs.model_name,
            cs.created_at,
            cs.updated_at,
            COALESCE(MAX(sv.last_visit), '1970-01-01 00:00:00') as last_visit,
            (SELECT COUNT(*) FROM messages
             WHERE session_id = cs.id AND role = 'assistant'
             AND timestamp > COALESCE(MAX(sv.last_visit), '1970-01-01 00:00:00')) as unread_count,
            (SELECT COUNT(*) FROM messages WHERE session_id = cs.id) as message_count
        FROM chat_sessions cs
        LEFT JOIN session_visits sv ON cs.id = sv.session_id AND sv.user_id = %s
        WHERE cs.user_id = %s
        GROUP BY cs.id, cs.title, cs.model_name, cs.created_at, cs.updated_at
        ORDER BY cs.updated_at DESC
        """,
            (user_id, user_id),
        )

        sessions = []
        for row in c.fetchall():
            session_dict = dict(row)
            session_dict["has_unread"] = session_dict["unread_count"] > 0
            # Format timestamps as ISO
            for key in ("created_at", "updated_at", "last_visit"):
                if session_dict.get(key):
                    dt = session_dict[key]
                    if hasattr(dt, "isoformat"):
                        if current_app.config.get("TIMEZONE") and dt.tzinfo is None:
                            dt = current_app.config["TIMEZONE"].localize(dt)
                        session_dict[key] = dt.isoformat()
            sessions.append(session_dict)
        return sessions


def get_session_messages(
    session_id: str, since: str | None = None, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    """Get messages for a session with pagination."""
    with get_db() as conn:
        c = conn.cursor()
        if since:
            if "T" in since:
                since = since.replace("T", " ")[:19]
            c.execute(
                """
            SELECT id, role, content, file_type, file_name, file_path,
                   CASE WHEN file_path IS NOT NULL THEN NULL ELSE file_data END AS file_data,
                   timestamp, model_name, response_time, mm_time, gen_time,
                   mm_model, gen_model, response_style, completion_tokens
            FROM messages
            WHERE session_id = %s AND timestamp > %s
            ORDER BY timestamp ASC, id ASC
            LIMIT %s OFFSET %s
            """,
                (session_id, since, limit, offset),
            )
        else:
            c.execute(
                """
            SELECT id, role, content, file_type, file_name, file_path,
                   CASE WHEN file_path IS NOT NULL THEN NULL ELSE file_data END AS file_data,
                   timestamp, model_name, response_time, mm_time, gen_time,
                   mm_model, gen_model, response_style, completion_tokens
            FROM messages
            WHERE session_id = %s
            ORDER BY timestamp ASC, id ASC
            LIMIT %s OFFSET %s
            """,
                (session_id, limit, offset),
            )
        messages = []
        for row in c.fetchall():
            msg_dict = dict(row)
            if msg_dict.get("response_time"):
                try:
                    msg_dict["response_time"] = json.loads(msg_dict["response_time"])
                except Exception as e:
                    current_app.logger.debug(
                        f"Failed to parse response_time JSON for message {msg_dict.get('id')}: {e}"
                    )
            if msg_dict.get("timestamp"):
                dt = msg_dict["timestamp"]
                if hasattr(dt, "isoformat"):
                    if current_app.config.get("TIMEZONE") and dt.tzinfo is None:
                        dt = current_app.config["TIMEZONE"].localize(dt)
                    msg_dict["timestamp"] = dt.isoformat()
            # Strip base64 file_data from content JSON when file is on disk
            if msg_dict.get("file_path") and msg_dict.get("content"):
                try:
                    parsed = json.loads(msg_dict["content"])
                    if isinstance(parsed, list):
                        for item in parsed:
                            if isinstance(item, dict) and "file_data" in item:
                                item["file_data"] = None
                        msg_dict["content"] = json.dumps(parsed, ensure_ascii=False)
                except Exception as e:
                    current_app.logger.debug(
                        f"Failed to parse content JSON for message {msg_dict.get('id')}: {e}"
                    )
            messages.append(msg_dict)

        # Read file sizes from disk for messages with file_path
        upload_folder = current_app.config.get("UPLOAD_FOLDER", "data/uploads")
        for msg in messages:
            fp = msg.get("file_path")
            if fp:
                full = os.path.join(upload_folder, fp) if not os.path.isabs(fp) else fp
                try:
                    msg["file_size"] = os.path.getsize(full)
                except OSError:
                    msg["file_size"] = 0
            else:
                msg["file_size"] = 0

        return messages


def create_session(user_id, title=None, lang="ru"):
    """Create new session with translated title."""
    session_id = str(uuid.uuid4())
    current_time = get_current_time_for_db()
    if title is None:
        from flask import current_app
        from flask_babel import force_locale

        with current_app.app_context(), force_locale(lang):
            title = _("New session")
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        INSERT INTO chat_sessions (id, user_id, title, model_name, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
            (session_id, user_id, title, "auto", current_time, current_time),
        )
        c.execute(
            """
        INSERT INTO session_visits (user_id, session_id, last_visit)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, session_id) DO UPDATE SET last_visit = %s
        """,
            (user_id, session_id, current_time, current_time),
        )
    return session_id


def update_session_title(session_id, first_message, file_name=None):
    """Update session title based on first message."""
    if first_message and first_message.strip():
        title = first_message[:40] + ("..." if len(first_message) > 40 else "")
    elif file_name:
        title = file_name[:40] + ("..." if len(file_name) > 40 else "")
    else:
        title = _("New session")
    current_time = get_current_time_for_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        UPDATE chat_sessions
        SET title = %s, updated_at = %s
        WHERE id = %s
        """,
            (title, current_time, session_id),
        )
    return title


def _publish_message_event(session_id, message_id, role, user_id=None):
    """Publish a message_new event via SSE (best-effort, no-op if publisher unavailable).

    Includes the full message data to avoid a separate 50-message fetch on the client.
    file_data is NULLed when file_path exists (same as get_session_messages).
    """
    try:
        from .events import get_events_publisher

        publisher = get_events_publisher()
        if publisher is None:
            return
        if user_id is None:
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT user_id FROM chat_sessions WHERE id = %s", (session_id,))
                row = c.fetchone()
                if not row:
                    return
                user_id = row["user_id"]
        if user_id:
            # Fetch full message data for inline delivery
            msg_data = None
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    """
                    SELECT id, role, content, file_type, file_name, file_path,
                           CASE WHEN file_path IS NOT NULL THEN NULL ELSE file_data END AS file_data,
                           timestamp, model_name, response_time, mm_time, gen_time,
                           mm_model, gen_model, response_style, completion_tokens
                    FROM messages WHERE id = %s AND session_id = %s
                    """,
                    (message_id, session_id),
                )
                row = c.fetchone()
                if row:
                    msg_data = dict(row)
                    # Serialize timestamp and response_time
                    if msg_data.get("timestamp"):
                        dt = msg_data["timestamp"]
                        if hasattr(dt, "isoformat"):
                            msg_data["timestamp"] = dt.isoformat()
                    if msg_data.get("response_time"):
                        with contextlib.suppress(Exception):
                            msg_data["response_time"] = json.loads(msg_data["response_time"])
                    # Strip base64 from content JSON when file_path exists
                    if msg_data.get("file_path") and msg_data.get("content"):
                        with contextlib.suppress(Exception):
                            parsed = json.loads(msg_data["content"])
                            if isinstance(parsed, list):
                                for item in parsed:
                                    if isinstance(item, dict) and "file_data" in item:
                                        item["file_data"] = None
                            msg_data["content"] = json.dumps(parsed, ensure_ascii=False)

            publisher.publish(
                user_id,
                "message_new",
                {
                    "message_id": message_id,
                    "session_id": session_id,
                    "role": role,
                    "message": msg_data,
                },
            )
    except Exception:
        pass


def save_message(
    session_id,
    role,
    content,
    file_data=None,
    file_type=None,
    file_name=None,
    file_path=None,
    model_name=None,
    response_time=None,
    mm_time=None,
    gen_time=None,
    mm_model=None,
    gen_model=None,
    user_id=None,
    response_style=None,
    completion_tokens=None,
):
    """Save a message to the database.

    If *user_id* is provided, publishes a ``message_new`` SSE event.
    """
    if response_style is None:
        response_style = "neutral"
    with get_db() as conn:
        c = conn.cursor()
        current_time = get_current_time_for_db()
        if file_data and not file_path:
            from .utils import save_uploaded_file

            file_path = save_uploaded_file(
                file_data=file_data,
                filename=file_name,
                session_id=session_id,
                upload_folder=current_app.config["UPLOAD_FOLDER"],
            )
        if response_time and isinstance(response_time, dict):
            response_time = json.dumps(response_time, ensure_ascii=False)
        elif response_time is not None and not isinstance(response_time, str):
            response_time = str(response_time)
        c.execute(
            """
        INSERT INTO messages (
            session_id, role, content, file_data, file_type, file_name, file_path,
            model_name, timestamp, response_time, mm_time, gen_time,
            mm_model, gen_model, response_style, completion_tokens
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                session_id,
                role,
                content,
                file_data,
                file_type,
                file_name,
                file_path,
                model_name,
                current_time,
                response_time,
                mm_time,
                gen_time,
                mm_model,
                gen_model,
                response_style,
                completion_tokens,
            ),
        )
        message_id = c.lastrowid
        c.execute(
            """
        UPDATE chat_sessions
        SET updated_at = %s
        WHERE id = %s
        """,
            (current_time, session_id),
        )
    _publish_message_event(session_id, message_id, role, user_id)
    return message_id


def get_last_session(user_id):
    """Get user's last session."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT last_session_id FROM user_sessions WHERE user_id = %s", (user_id,))
        row = c.fetchone()
        return row["last_session_id"] if row else None


def set_last_session(user_id, session_id):
    """Set user's last session."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        INSERT INTO user_sessions (user_id, last_session_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET last_session_id = %s
        """,
            (user_id, session_id, session_id),
        )


def delete_session_and_messages(session_id, user_id, upload_folder=None):
    """Delete a session, its messages, and associated files from disk.
    Also updates user_storage quota by subtracting file sizes.
    """
    total_deleted_bytes = 0

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM chat_sessions WHERE id = %s", (session_id,))
        row = c.fetchone()
        if not row or row["user_id"] != user_id:
            return False
        c.execute("SELECT file_path FROM messages WHERE session_id = %s AND file_path IS NOT NULL", (session_id,))
        rows = c.fetchall()
        for row in rows:
            file_path = row["file_path"]
            if file_path:
                if upload_folder and not os.path.isabs(file_path):
                    full_path = os.path.join(upload_folder, file_path)
                else:
                    full_path = file_path
                if os.path.exists(full_path):
                    try:
                        total_deleted_bytes += os.path.getsize(full_path)
                        os.remove(full_path)
                    except Exception as e:
                        import logging

                        logging.getLogger(__name__).warning(f"Failed to delete file {full_path}: {e}")

        c.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
        c.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
        c.execute("SELECT COUNT(*) as cnt FROM chat_sessions WHERE user_id = %s", (user_id,))
        count = c.fetchone()["cnt"]
        if count == 0:
            c.execute("DELETE FROM user_sessions WHERE user_id = %s", (user_id,))
        else:
            c.execute("SELECT last_session_id FROM user_sessions WHERE user_id = %s", (user_id,))
            row = c.fetchone()
            if row and row["last_session_id"] == session_id:
                c.execute(
                    "SELECT id FROM chat_sessions WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1", (user_id,)
                )
                new_last = c.fetchone()
                if new_last:
                    c.execute(
                        "UPDATE user_sessions SET last_session_id = %s WHERE user_id = %s", (new_last["id"], user_id)
                    )
                else:
                    c.execute("DELETE FROM user_sessions WHERE user_id = %s", (user_id,))
        c.execute("DELETE FROM session_visits WHERE session_id = %s", (session_id,))

    # Update storage quota (subtract deleted file sizes)
    if total_deleted_bytes > 0:
        update_user_storage(user_id, -total_deleted_bytes)

    # If user has no remaining messages, clean up SLM database
    _cleanup_slm_if_empty(user_id)

    return True


def _cleanup_slm_if_empty(user_id: str) -> None:
    """Delete the user's SLM database if they have no messages remaining."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """SELECT COUNT(*) as cnt FROM messages m
               JOIN chat_sessions cs ON m.session_id = cs.id
               WHERE cs.user_id = %s""",
            (user_id,),
        )
        if c.fetchone()["cnt"] > 0:
            return

    slm_dir = os.path.join("/app/data/slm", user_id, ".superlocalmemory")
    if os.path.exists(slm_dir):
        with contextlib.suppress(Exception):
            import shutil

            shutil.rmtree(slm_dir, ignore_errors=True)
            import logging

            logging.getLogger(__name__).info(f"SLM data deleted for user {user_id} (no messages left)")


def update_session_visit(user_id, session_id):
    """Update session last visit timestamp."""
    current_time = get_current_time_for_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        INSERT INTO session_visits (user_id, session_id, last_visit)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, session_id) DO UPDATE SET last_visit = %s
        """,
            (user_id, session_id, current_time, current_time),
        )


# Index status constants
INDEX_STATUS_PENDING = "pending"
INDEX_STATUS_INDEXING = "indexing"
INDEX_STATUS_INDEXED = "indexed"
INDEX_STATUS_FAILED = "failed"


def get_user_documents(user_id):
    """Get all documents for a user, including index status, processing time and embedding model."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        SELECT id, filename, file_size, file_ext, file_path, uploaded_at,
               index_status, indexed_at, indexing_started_at, embedding_model
        FROM documents
        WHERE user_id = %s
        ORDER BY uploaded_at DESC
        """,
            (user_id,),
        )
        documents = [dict(row) for row in c.fetchall()]
        tz = current_app.config.get("TIMEZONE")
        now = datetime.now(tz).replace(tzinfo=None) if tz else datetime.utcnow()
        for doc in documents:
            indexed_dt = None
            indexing_started_dt = None
            uploaded_dt = None
            if doc.get("uploaded_at"):
                dt = doc["uploaded_at"]
                if hasattr(dt, "replace"):
                    uploaded_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
                else:
                    with contextlib.suppress(Exception):
                        uploaded_dt = datetime.strptime(str(doc["uploaded_at"])[:19], "%Y-%m-%d %H:%M:%S")
            if doc.get("indexed_at"):
                dt = doc["indexed_at"]
                if hasattr(dt, "replace"):
                    indexed_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
                else:
                    with contextlib.suppress(Exception):
                        indexed_dt = datetime.strptime(str(doc["indexed_at"])[:19], "%Y-%m-%d %H:%M:%S")
            if doc.get("indexing_started_at"):
                dt = doc["indexing_started_at"]
                if hasattr(dt, "replace"):
                    indexing_started_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
                else:
                    with contextlib.suppress(Exception):
                        indexing_started_dt = datetime.strptime(
                            str(doc["indexing_started_at"])[:19], "%Y-%m-%d %H:%M:%S"
                        )
            processing_time = None
            status = doc.get("index_status")
            if status == INDEX_STATUS_INDEXED and indexed_dt and indexing_started_dt:
                delta = indexed_dt - indexing_started_dt
                processing_time = delta.total_seconds() / 60.0
            elif status == INDEX_STATUS_INDEXING and indexing_started_dt:
                delta = now - indexing_started_dt
                processing_time = delta.total_seconds() / 60.0
            # NOTE: For indexed documents without indexing_started_at, we don't fall back
            # to uploaded_dt because the difference (indexed_at - uploaded_at) can be days/weeks
            # and does not represent actual processing time.
            if processing_time is not None and processing_time < 0:
                processing_time = abs(processing_time)
            doc["processing_time"] = processing_time
            if uploaded_dt:
                if tz:
                    uploaded_dt = tz.localize(uploaded_dt)
                doc["uploaded_at"] = uploaded_dt.isoformat()
            if indexed_dt:
                if tz:
                    indexed_dt = tz.localize(indexed_dt)
                doc["indexed_at"] = indexed_dt.isoformat()
            if indexing_started_dt:
                if tz:
                    indexing_started_dt = tz.localize(indexing_started_dt)
                doc["indexing_started_at"] = indexing_started_dt.isoformat()
        return documents


def save_document(user_id, doc_id, filename, file_size, file_ext, file_path):
    """Save document metadata to database."""
    current_time = get_current_time_for_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        INSERT INTO documents (id, user_id, filename, file_size, file_ext, file_path, uploaded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
            (doc_id, user_id, filename, file_size, file_ext, file_path, current_time),
        )


def update_document_index_status(doc_id, index_status, indexed_at=None, indexing_started_at=None, embedding_model=None):
    """Update document index status."""
    current_time = get_current_time_for_db()
    with get_db() as conn:
        c = conn.cursor()
        if index_status == INDEX_STATUS_INDEXED and indexed_at is None:
            indexed_at = current_time
        if embedding_model:
            if indexing_started_at is not None:
                c.execute(
                    """
                UPDATE documents
                SET index_status = %s, indexed_at = %s, indexing_started_at = %s, embedding_model = %s
                WHERE id = %s
                """,
                    (index_status, indexed_at, indexing_started_at, embedding_model, doc_id),
                )
            else:
                c.execute(
                    """
                UPDATE documents
                SET index_status = %s, indexed_at = %s, embedding_model = %s
                WHERE id = %s
                """,
                    (index_status, indexed_at, embedding_model, doc_id),
                )
        else:
            if indexing_started_at is not None:
                c.execute(
                    """
                UPDATE documents
                SET index_status = %s, indexed_at = %s, indexing_started_at = %s
                WHERE id = %s
                """,
                    (index_status, indexed_at, indexing_started_at, doc_id),
                )
            else:
                c.execute(
                    """
                UPDATE documents
                SET index_status = %s, indexed_at = %s
                WHERE id = %s
                """,
                    (index_status, indexed_at, doc_id),
                )


def get_document(doc_id, user_id):
    """Get document metadata by ID and user."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        SELECT id, filename, file_size, file_ext, file_path, index_status, indexed_at, uploaded_at, embedding_model
        FROM documents
        WHERE id = %s AND user_id = %s
        """,
            (doc_id, user_id),
        )
        row = c.fetchone()
        return dict(row) if row else None


def delete_document(doc_id, user_id):
    """Delete document metadata from database and update storage quota."""
    file_size = 0
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT file_size FROM documents WHERE id = %s AND user_id = %s", (doc_id, user_id))
        row = c.fetchone()
        if row:
            file_size = row["file_size"] or 0
        c.execute("DELETE FROM documents WHERE id = %s AND user_id = %s", (doc_id, user_id))

    if file_size > 0:
        update_user_storage(user_id, -file_size)


def _extract_text_content(content: str) -> str:
    """Extract only text from JSON content, stripping file_data and markers."""
    if not content:
        return content
    if not (content.startswith("[") or content.startswith("{")):
        stripped = content.strip()
        stripped = re.sub(r"^\[-(?:IMAGE|VIDEO|REASONING|RAG|CAMERA|IMAGE-EDIT)-\]\s*", "", stripped)
        return stripped
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            texts = []
            for item in parsed:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
            joined = " ".join(t for t in texts if t).strip()
            return joined if joined else ""
        if isinstance(parsed, dict):
            if "text" in parsed and "prefix" in parsed:
                return parsed["text"]  # type: ignore[no-any-return]
            if "file_data" in parsed:
                parsed["file_data"] = "[IMAGE DATA]"
                return json.dumps(parsed, ensure_ascii=False)
            return json.dumps(parsed, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        pass
    return content.strip()


def _has_marker(content: str) -> bool:
    """Check if raw content contains a generation marker before any stripping."""
    if not content:
        return False
    markers = ["[-VIDEO-]", "[-IMAGE-]", "[-REASONING-]", "[-RAG-]", "[-CAMERA-]", "[-IMAGE-EDIT-]"]
    return any(content.strip().startswith(m) for m in markers)


def get_session_text_history(session_id, max_tokens=None, max_messages=None):
    """Get session messages for context building (text only).
    Filters out pairs of user+assistant messages where the assistant
    responded with a generation marker ([-VIDEO-], [-IMAGE-], etc.)
    to prevent the router from copying old markers into new responses.
    """
    limit = max_messages or 200
    messages = get_session_messages(session_id, limit=limit)

    # Filter out user+assistant pairs where assistant replied with a marker
    filtered = []
    skip_next = False
    for i, msg in enumerate(messages):
        if skip_next:
            skip_next = False
            continue
        if i + 1 < len(messages) and messages[i + 1]["role"] == "assistant" and _has_marker(messages[i + 1].get("content", "")):
                skip_next = True
                continue
        filtered.append(msg)

    messages = filtered

    if max_tokens is not None:
        from .utils import estimate_tokens

        result = []
        total = 0
        for msg in reversed(messages):
            text_content = _extract_text_content(msg.get("content", ""))
            tokens = estimate_tokens(text_content)
            if total + tokens > max_tokens:
                break
            result.append({**msg, "content": text_content})
            total += tokens
        return list(reversed(result))
    return messages
