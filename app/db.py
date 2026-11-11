# app/db.py
# Database functions - PostgreSQL only
import contextlib
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

from flask import current_app
from flask_babel import gettext as _

from app.database import get_db
from app.monitoring.archive import archive_documents, archive_messages, archive_session


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


def get_user_storage_limit(user_id: str) -> int | None:
    """Per-user upload limit in bytes; ``None`` means the global default applies."""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT limit_bytes FROM user_storage WHERE user_id = %s", (user_id,))
            row = c.fetchone()
            return int(row["limit_bytes"]) if row and row.get("limit_bytes") else None
    except Exception:
        return None


def set_user_storage_limit(user_id: str, limit_bytes: int | None) -> None:
    """Store a per-user upload limit; ``None`` clears the override.

    Creates the quota row when the user has not uploaded anything yet, and never
    touches ``used_bytes`` — the limit and the usage are independent.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO user_storage (user_id, used_bytes, limit_bytes)
            VALUES (%s, 0, %s)
            ON CONFLICT (user_id) DO UPDATE SET limit_bytes = EXCLUDED.limit_bytes
        """,
            (user_id, limit_bytes),
        )


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
            (SELECT COUNT(*) FROM messages WHERE session_id = cs.id) as message_count,
            (SELECT COALESCE(SUM(prompt_tokens), 0) FROM messages WHERE session_id = cs.id) as total_prompt_tokens,
            (SELECT COALESCE(SUM(completion_tokens), 0) FROM messages WHERE session_id = cs.id) as total_completion_tokens
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


def _strip_content_attachment_payload(content: str | None, row_file_path: str | None) -> str | None:
    """Remove base64 payloads from the attachment parts of a message content JSON.

    A part with its own ``file_path`` (saved to disk) is stripped in place. A
    pathless part reuses the row-level ``file_path`` when it is the *first*
    attachment part — legacy rows stored the primary attachment both in the row
    columns and inside the content JSON without a part-level path. Parts that
    end up without any path keep their base64 (they are the only source).
    """
    if not content or not row_file_path:
        return content
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return content
    if not isinstance(parsed, list):
        return content
    first_attachment_seen = False
    for item in parsed:
        if not isinstance(item, dict) or "file_data" not in item:
            continue
        if not first_attachment_seen:
            first_attachment_seen = True
            if not item.get("file_path"):
                item["file_path"] = row_file_path
        if item.get("file_path"):
            item["file_data"] = None
    return json.dumps(parsed, ensure_ascii=False)


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
                   mm_model, gen_model, response_style, completion_tokens, model_type, prompt_tokens
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
                   mm_model, gen_model, response_style, completion_tokens, model_type, prompt_tokens
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
            # Strip base64 file_data from content JSON, substituting the row
            # file_path for legacy first attachment parts that lack a path of
            # their own (old chat images). See _strip_content_attachment_payload.
            msg_dict["content"] = _strip_content_attachment_payload(msg_dict.get("content"), msg_dict.get("file_path"))
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
                           mm_model, gen_model, response_style, completion_tokens, model_type, prompt_tokens
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
                    msg_data["content"] = _strip_content_attachment_payload(
                        msg_data.get("content"), msg_data.get("file_path")
                    )

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
    model_type=None,
    prompt_tokens=None,
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
            mm_model, gen_model, response_style, completion_tokens, model_type, prompt_tokens
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
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
                model_type,
                prompt_tokens,
            ),
        )
        # PostgreSQL: lastrowid is always 0 — the real id comes from RETURNING.
        message_id = c.fetchone()["id"]
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


def delete_session_and_messages(session_id, user_id, upload_folder=None, archive=True):
    """Delete a session, its messages, and associated files from disk.
    Also updates user_storage quota by subtracting file sizes.

    File sources are both the messages.file_path column AND file_path fields
    inside the content JSON (extra multi-attachment chat images are stored
    there — the column only holds the first image).

    Unless *archive* is false, the session and its messages are first copied to
    the monitoring archive so deleting a chat does not erase its history.
    """
    total_deleted_bytes = 0
    deleted_rel_paths: set[str] = set()

    def _remove_file(rel_path: str) -> None:
        nonlocal total_deleted_bytes
        if not rel_path or rel_path in deleted_rel_paths:
            return
        deleted_rel_paths.add(rel_path)
        full_path = os.path.join(upload_folder, rel_path) if upload_folder and not os.path.isabs(rel_path) else rel_path
        if os.path.exists(full_path):
            try:
                total_deleted_bytes += os.path.getsize(full_path)
                os.remove(full_path)
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Failed to delete file {full_path}: {e}")

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM chat_sessions WHERE id = %s", (session_id,))
        row = c.fetchone()
        if not row or row["user_id"] != user_id:
            return False
        c.execute("SELECT file_path, content FROM messages WHERE session_id = %s", (session_id,))
        rows = c.fetchall()
        for row in rows:
            _remove_file(row["file_path"])
            content = row["content"]
            if not content:
                continue
            try:
                parsed = json.loads(content)
            except Exception:
                continue
            if not isinstance(parsed, list):
                continue
            for item in parsed:
                if isinstance(item, dict) and item.get("file_path"):
                    _remove_file(item["file_path"])

        if archive:
            archive_session(c, session_id)
            archive_messages(c, session_id)

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


def _localize_document_dates(documents):
    """Add processing time and timezone-aware timestamps to document rows."""
    tz = current_app.config.get("TIMEZONE")
    now = datetime.now(tz).replace(tzinfo=None) if tz else datetime.now(UTC).replace(tzinfo=None)
    for doc in documents:
        indexed_dt = None
        indexing_started_dt = None
        uploaded_dt = None
        if doc.get("uploaded_at"):
            dt = doc["uploaded_at"]
            if isinstance(dt, datetime):
                uploaded_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
            else:
                with contextlib.suppress(Exception):
                    uploaded_dt = datetime.strptime(str(doc["uploaded_at"])[:19], "%Y-%m-%d %H:%M:%S")
        if doc.get("indexed_at"):
            dt = doc["indexed_at"]
            if isinstance(dt, datetime):
                indexed_dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
            else:
                with contextlib.suppress(Exception):
                    indexed_dt = datetime.strptime(str(doc["indexed_at"])[:19], "%Y-%m-%d %H:%M:%S")
        if doc.get("indexing_started_at"):
            dt = doc["indexing_started_at"]
            if isinstance(dt, datetime):
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
            processing_time = delta.total_seconds()
        elif status == INDEX_STATUS_INDEXING and indexing_started_dt:
            delta = now - indexing_started_dt
            processing_time = delta.total_seconds()
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


def get_user_documents(user_id, include_shared=False, with_share_counts=False):
    """Get all documents for a user, including index status, processing time and embedding model.

    With ``include_shared`` the documents other users shared with this one are
    appended, each carrying ``shared_by`` (the owner's login); their file paths
    and index rows stay the owner's, and the caller only ever reads them.

    With ``with_share_counts`` every owned document carries ``shared_with`` (the
    logins it is open to) and ``shared_count``, so the panel can mark it. The
    extra query is opt-in: most callers only need the documents themselves.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        SELECT id, filename, file_size, file_ext, file_path, uploaded_at,
               index_status, indexed_at, indexing_started_at, embedding_model, description_model,
               folder_id
        FROM documents
        WHERE user_id = %s
        ORDER BY uploaded_at DESC
        """,
            (user_id,),
        )
        documents = [dict(row) for row in c.fetchall()]
        for doc in documents:
            doc["shared_by"] = None
            doc["shared_with"] = []
            doc["shared_count"] = 0
        if with_share_counts and documents:
            c.execute(
                "SELECT document_id, array_agg(shared_with ORDER BY shared_with) AS shared_with "
                "FROM document_shares WHERE owner_login = %s GROUP BY document_id",
                (user_id,),
            )
            open_to = {row["document_id"]: list(row["shared_with"] or []) for row in c.fetchall()}
            for doc in documents:
                readers = open_to.get(doc["id"]) or []
                doc["shared_with"] = readers
                doc["shared_count"] = len(readers)
        if include_shared:
            c.execute(
                """
            SELECT d.id, d.filename, d.file_size, d.file_ext, d.file_path, d.uploaded_at,
                   d.index_status, d.indexed_at, d.indexing_started_at, d.embedding_model,
                   d.description_model, d.folder_id, s.owner_login AS shared_by
            FROM document_shares s
            JOIN documents d ON d.id = s.document_id
            WHERE s.shared_with = %s
            ORDER BY d.uploaded_at DESC
            """,
                (user_id,),
            )
            shared_rows = [dict(row) for row in c.fetchall()]
            for doc in shared_rows:
                # The folder belongs to the owner and means nothing to the
                # recipient, who would silently lose the document from the panel.
                doc["folder_id"] = None
            documents.extend(shared_rows)
            documents.sort(key=lambda doc: str(doc.get("uploaded_at") or ""), reverse=True)
        return _localize_document_dates(documents)


def share_document(document_id, owner_login, shared_with):
    """Give ``shared_with`` read access to one of ``owner_login``'s documents."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO document_shares (document_id, owner_login, shared_with) VALUES (%s, %s, %s) "
            "ON CONFLICT (document_id, shared_with) DO NOTHING",
            (document_id, owner_login, shared_with),
        )


def unshare_document(document_id, shared_with):
    """Revoke one reader's access; returns True when a share was removed."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "DELETE FROM document_shares WHERE document_id = %s AND shared_with = %s",
            (document_id, shared_with),
        )
        return c.rowcount > 0


def get_document_shares(document_id):
    """Every reader a document is shared with, oldest first."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT shared_with, created_at FROM document_shares WHERE document_id = %s ORDER BY created_at",
            (document_id,),
        )
        return [dict(row) for row in c.fetchall()]


def get_shared_document(document_id, viewer_login):
    """A document shared with ``viewer_login`` (owner's fields plus ``shared_by``)."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        SELECT d.id, d.user_id, d.filename, d.file_size, d.file_ext, d.file_path, d.index_status,
               d.indexed_at, d.uploaded_at, d.embedding_model, d.folder_id, d.blob_sha256,
               d.stored_bytes, s.owner_login AS shared_by
        FROM document_shares s
        JOIN documents d ON d.id = s.document_id
        WHERE s.document_id = %s AND s.shared_with = %s
        """,
            (document_id, viewer_login),
        )
        row = c.fetchone()
        return dict(row) if row else None


def get_readable_document(document_id, viewer_login):
    """A document the viewer may read: their own, or one shared with them."""
    doc = get_document(document_id, viewer_login)
    if doc:
        return doc
    return get_shared_document(document_id, viewer_login)


def get_shared_document_ids(viewer_login):
    """Map of document id -> owner login for everything shared with the viewer."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT document_id, owner_login FROM document_shares WHERE shared_with = %s",
            (viewer_login,),
        )
        return {row["document_id"]: row["owner_login"] for row in c.fetchall()}


def retain_document_blob(sha256, file_path, size_bytes, mime=None):
    """Register one more reference to a content blob.

    Returns True when this call created the blob (the caller must write the
    file); False means an identical document already exists and the bytes are
    already on disk.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT sha256 FROM document_blobs WHERE sha256 = %s", (sha256,))
        if c.fetchone():
            c.execute("UPDATE document_blobs SET refcount = refcount + 1 WHERE sha256 = %s", (sha256,))
            return False
        c.execute(
            "INSERT INTO document_blobs (sha256, file_path, size_bytes, mime, refcount) VALUES (%s, %s, %s, %s, 1)",
            (sha256, file_path, size_bytes, mime),
        )
        return True


def release_document_blob(sha256):
    """Drop one reference to a content blob.

    Returns ``(removed, file_path)``: the file may be unlinked only when the last
    reference is gone, because every other document pointing at it is somebody
    else's own document, not a share.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT refcount, file_path FROM document_blobs WHERE sha256 = %s", (sha256,))
        row = c.fetchone()
        if not row:
            return False, None
        if row["refcount"] > 1:
            c.execute("UPDATE document_blobs SET refcount = refcount - 1 WHERE sha256 = %s", (sha256,))
            return False, row["file_path"]
        c.execute("DELETE FROM document_blobs WHERE sha256 = %s", (sha256,))
        return True, row["file_path"]


def find_indexed_document_by_blob(blob_sha256, exclude_doc_id):
    """Return an indexed document with the same content hash, if there is one.

    Its vectors can be copied instead of embedding the same chunks again; the
    document is the source, not a share — nothing of it becomes visible to the
    caller, only the vectors derived from bytes the caller uploaded anyway.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, user_id, filename, embedding_model FROM documents "
            "WHERE blob_sha256 = %s AND id <> %s AND index_status = %s "
            "ORDER BY indexed_at DESC NULLS LAST LIMIT 1",
            (blob_sha256, exclude_doc_id, INDEX_STATUS_INDEXED),
        )
        row = c.fetchone()
        return dict(row) if row else None


def document_blob_savings_bytes():
    """Bytes not stored because identical content was reused."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(size_bytes * (refcount - 1)), 0) AS saved FROM document_blobs")
        row = c.fetchone()
        return int((row["saved"] if row else 0) or 0)


def delete_document_resources(doc, login):
    """Delete one document: index entry, blob reference, file and DB row.

    The single deletion path for every caller (web route, folder delete, API):
    the file is unlinked only when this was the last document using it.
    """
    rag = current_app.modules.get("rag")
    if rag and rag.available:
        try:
            rag.delete_document(doc["id"], login)
        except Exception as e:
            current_app.logger.error(f"Failed to delete document from index: {e}")
    documents_folder = current_app.config["DOCUMENTS_FOLDER"]
    blob_sha256 = doc.get("blob_sha256")
    if blob_sha256:
        # Content-addressed: drop this user's reference; the bytes stay while
        # another document still points at them.
        removed, _relative = release_document_blob(blob_sha256)
        file_path = os.path.join(documents_folder, doc.get("file_path") or "")
        if removed:
            real_file_path = os.path.realpath(file_path)
            real_documents_folder = os.path.realpath(documents_folder)
            if real_file_path.startswith(real_documents_folder + os.sep) and os.path.exists(file_path):
                with contextlib.suppress(Exception):
                    os.remove(file_path)
    else:
        # Legacy row, stored before content addressing: the file is this
        # document's own.
        file_path = os.path.join(documents_folder, doc.get("file_path") or "")
        real_file_path = os.path.realpath(file_path)
        real_documents_folder = os.path.realpath(documents_folder)
        if real_file_path.startswith(real_documents_folder + os.sep) and os.path.exists(file_path):
            with contextlib.suppress(Exception):
                os.remove(file_path)
    delete_document(doc["id"], login)


def save_document(user_id, doc_id, filename, file_size, file_ext, file_path, folder_id=None,
                  blob_sha256=None, stored_bytes=None):
    """Save document metadata to database.

    ``stored_bytes`` is what this upload added to the disk: the file size when it
    created a new blob, 0 when an identical document already existed. The quota
    is charged on it, so a user pays for the bytes they actually store.
    """
    current_time = get_current_time_for_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        INSERT INTO documents (id, user_id, filename, file_size, file_ext, file_path, uploaded_at, folder_id,
                               blob_sha256, stored_bytes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                doc_id,
                user_id,
                filename,
                file_size,
                file_ext,
                file_path,
                current_time,
                folder_id,
                blob_sha256,
                stored_bytes if stored_bytes is not None else file_size,
            ),
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
        SELECT id, filename, file_size, file_ext, file_path, index_status, indexed_at, uploaded_at,
               embedding_model, blob_sha256, stored_bytes
        FROM documents
        WHERE id = %s AND user_id = %s
        """,
            (doc_id, user_id),
        )
        row = c.fetchone()
        return dict(row) if row else None


def save_folder(user_id, folder_id, name):
    """Insert a new document folder (single-level, owned by the user)."""
    current_time = get_current_time_for_db()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        INSERT INTO document_folders (id, user_id, name, created_at)
        VALUES (%s, %s, %s, %s)
        """,
            (folder_id, user_id, name, current_time),
        )


def get_user_document_names(user_id, limit=5):
    """Count the user's documents and return the newest few file names.

    Feeds the router's library hint: a cheap two-column read (no processing
    metadata), so the classifier knows a document search is possible before it
    decides between reasoning and documents.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) AS total FROM documents WHERE user_id = %s", (user_id,))
        total = int(c.fetchone()["total"] or 0)
        if not total or limit <= 0:
            return total, []
        c.execute(
            """
        SELECT filename
        FROM documents
        WHERE user_id = %s
        ORDER BY uploaded_at DESC
        LIMIT %s
        """,
            (user_id, limit),
        )
        return total, [row["filename"] for row in c.fetchall()]


def get_user_folders(user_id):
    """List the user's folders (oldest first)."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        SELECT id, name, created_at
        FROM document_folders
        WHERE user_id = %s
        ORDER BY created_at ASC, name ASC
        """,
            (user_id,),
        )
        return [dict(row) for row in c.fetchall()]


def get_folder(folder_id, user_id):
    """Get a folder that belongs to the user, or None."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        SELECT id, name
        FROM document_folders
        WHERE id = %s AND user_id = %s
        """,
            (folder_id, user_id),
        )
        row = c.fetchone()
        return dict(row) if row else None


def rename_folder(folder_id, user_id, name):
    """Rename a folder owned by the user. Returns True when renamed."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        UPDATE document_folders
        SET name = %s
        WHERE id = %s AND user_id = %s
        """,
            (name, folder_id, user_id),
        )
        return c.rowcount > 0


def delete_folder(folder_id, user_id):
    """Delete a folder row (documents are removed by the caller). Returns True when deleted."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        DELETE FROM document_folders
        WHERE id = %s AND user_id = %s
        """,
            (folder_id, user_id),
        )
        return c.rowcount > 0


def get_folder_documents(user_id, folder_id):
    """List the documents inside a folder (metadata only)."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        SELECT id, filename, file_size, file_path, blob_sha256
        FROM documents
        WHERE user_id = %s AND folder_id = %s
        """,
            (user_id, folder_id),
        )
        return [dict(row) for row in c.fetchall()]


def set_document_folder(doc_id, user_id, folder_id):
    """Move a document into a folder (None moves it to the root). Returns True when moved."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
        UPDATE documents
        SET folder_id = %s
        WHERE id = %s AND user_id = %s
        """,
            (folder_id, doc_id, user_id),
        )
        return c.rowcount > 0


def delete_document(doc_id, user_id):
    """Delete document metadata from database and update storage quota.

    The document is archived first, so its history survives the deletion.
    """
    file_size = 0
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT file_size FROM documents WHERE id = %s AND user_id = %s", (doc_id, user_id))
        row = c.fetchone()
        if row:
            file_size = row["file_size"] or 0
        archive_documents(c, user_id, [doc_id])
        c.execute("DELETE FROM document_shares WHERE document_id = %s", (doc_id,))
        c.execute("DELETE FROM documents WHERE id = %s AND user_id = %s", (doc_id, user_id))
        _delete_kg_rows(c, doc_id, user_id)

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


def get_session_recent_history(session_id, limit=6, exclude_message_id=None):
    """Return the NEWEST messages of a session (raw, text not extracted).

    Same generation-marker pair filter as :func:`get_session_text_history`
    (applied to the tail) so the router never copies old markers into new
    responses. ``limit`` bounds the number of returned messages.
    """
    if not session_id:
        return []
    with get_db() as conn:
        c = conn.cursor()
        # model_type lets the caller tell a real answer from a system notice
        # (a failed search is not an answer to the question).
        c.execute(
            "SELECT id, role, content, model_type FROM messages WHERE session_id = %s ORDER BY id DESC LIMIT %s",
            (session_id, 200),
        )
        rows = [dict(r) for r in c.fetchall()]
    rows.reverse()
    if exclude_message_id is not None:
        rows = [m for m in rows if m.get("id") != exclude_message_id]
    filtered = []
    skip_next = False
    for i, msg in enumerate(rows):
        if skip_next:
            skip_next = False
            continue
        if i + 1 < len(rows) and rows[i + 1]["role"] == "assistant" and _has_marker(rows[i + 1].get("content", "")):
            skip_next = True
            continue
        filtered.append(msg)
    return filtered[-limit:]


def get_session_text_history(session_id, max_tokens=None, max_messages=None, return_meta=False):
    """Get session messages for context building (text only).
    Filters out pairs of user+assistant messages where the assistant
    responded with a generation marker ([-VIDEO-], [-IMAGE-], etc.)
    to prevent the router from copying old markers into new responses.

    When ``return_meta`` is True, returns ``(messages, meta)`` where meta
    holds ``dropped_count`` (messages trimmed from the front by the token
    budget), ``oldest_kept_id`` (id of the oldest retained message, None if
    the history is empty) and ``total_count`` (fetched messages after the
    generation-marker filter).
    """
    limit = max_messages or 200
    messages = get_session_messages(session_id, limit=limit)

    # A system notice (a failed search, an expired task) answered nothing. Fed to
    # the model as an assistant turn it reads as a reply, and a measured session
    # showed the model copying the notice verbatim into its next answer.
    messages = [message for message in messages if str(message.get("model_type") or "") != "system"]

    # Filter out user+assistant pairs where assistant replied with a marker
    filtered = []
    skip_next = False
    for i, msg in enumerate(messages):
        if skip_next:
            skip_next = False
            continue
        if (
            i + 1 < len(messages)
            and messages[i + 1]["role"] == "assistant"
            and _has_marker(messages[i + 1].get("content", ""))
        ):
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
        kept = list(reversed(result))
        # The cut is made from the front, so the oldest kept message can be an
        # answer whose question was dropped — the model then reads a reply to
        # nothing. Drop such orphan answers (and any duplicate roles after them).
        while kept and kept[0]["role"] != "user":
            kept.pop(0)
    else:
        kept = messages

    if not return_meta:
        return kept

    meta = {
        "dropped_count": max(0, len(messages) - len(kept)),
        "oldest_kept_id": kept[0]["id"] if kept else None,
        "total_count": len(messages),
    }
    return kept, meta


def get_session_summary(session_id):
    """Return the rolling session summary (summary + summary_upto_id) or None."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT summary, summary_upto_id FROM chat_sessions WHERE id = %s", (session_id,))
        row = c.fetchone()
        if not row or not row["summary"]:
            return None
        return {"summary": row["summary"], "summary_upto_id": row["summary_upto_id"]}


def update_session_summary(session_id, summary, summary_upto_id):
    """Store the rolling session summary and the message id it covers up to."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            UPDATE chat_sessions
            SET summary = %s, summary_upto_id = %s, updated_at = %s
            WHERE id = %s
            """,
            (summary, summary_upto_id, get_current_time_for_db(), session_id),
        )


# ── Branding settings (admin "Personalization" tab) ─────────────


def _branding_now() -> str:
    """Current DB timestamp; falls back to naive now() outside an app context."""
    try:
        from app.utils import get_current_time_in_timezone_for_db

        return get_current_time_in_timezone_for_db()
    except RuntimeError:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_branding_settings() -> dict:
    """Return the singleton branding row (always exists after init_db)."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT logo_path, logo_updated_at, site_name_ru, site_name_en, updated_at FROM branding_settings WHERE id = 1"
        )
        row = c.fetchone()
        if not row:
            return {
                "logo_path": None,
                "logo_updated_at": None,
                "site_name_ru": "",
                "site_name_en": "",
                "updated_at": None,
            }
        return {
            "logo_path": row["logo_path"],
            "logo_updated_at": row["logo_updated_at"],
            "site_name_ru": row["site_name_ru"] or "",
            "site_name_en": row["site_name_en"] or "",
            "updated_at": row["updated_at"],
        }


def set_branding_logo(file_path: str | None) -> None:
    """Store (or clear with None) the custom logo path; updates the timestamp."""
    now = _branding_now()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE branding_settings SET logo_path = %s, logo_updated_at = %s, updated_at = %s WHERE id = 1",
            (file_path, now if file_path else None, now),
        )


def set_branding_site_names(site_name_ru: str, site_name_en: str) -> None:
    """Store both localized site names; empty values mean 'use the default brand name'."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE branding_settings SET site_name_ru = %s, site_name_en = %s, updated_at = %s WHERE id = 1",
            (site_name_ru, site_name_en, _branding_now()),
        )


# ── Knowledge graph (v13.2) ────────────────────────────────────────────
# The graph is derived data: entities, the chunks that mention them, typed
# relations between them, and a document layer built from chunk centroids.
# Every row is user-scoped; documents shared with another account are reached
# through the same scopes the vector search uses.


def upsert_kg_node(user_id, kind, name, normalized, lemmas, description="", source_kind="document"):
    """Insert or merge an entity, returning its id.

    A node is identified by its normalised name and its kind, so "ООО
    «Вектор-Тех»" and "Вектор-Тех" land on one node instead of two.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO kg_nodes (user_id, kind, name, normalized, lemmas, description, source_kind)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, normalized, kind) DO UPDATE
            SET lemmas = ARRAY(SELECT DISTINCT unnest(kg_nodes.lemmas || EXCLUDED.lemmas)),
                description = CASE WHEN EXCLUDED.description <> '' THEN EXCLUDED.description
                                   ELSE kg_nodes.description END
            RETURNING id
            """,
            (user_id, kind, name, normalized, list(lemmas or []), description, source_kind),
        )
        return str(c.fetchone()["id"])


def add_kg_occurrence(node_id, user_id, doc_id, chunk_index, mention_text=""):
    """Record that a node is mentioned in one chunk (idempotent)."""
    with get_db() as conn:
        conn.cursor().execute(
            """
            INSERT INTO kg_occurrences (node_id, user_id, doc_id, chunk_index, mention_text)
            VALUES (%s::uuid, %s, %s, %s, %s)
            ON CONFLICT (node_id, doc_id, chunk_index) DO NOTHING
            """,
            (node_id, user_id, doc_id, chunk_index, mention_text),
        )


def upsert_kg_edge(user_id, src_id, dst_id, predicate, doc_id, chunk_index, evidence="", source_kind="document"):
    """Insert a relation, or raise the weight of the identical one."""
    with get_db() as conn:
        conn.cursor().execute(
            """
            INSERT INTO kg_edges (user_id, src_id, dst_id, predicate, doc_id, chunk_index, evidence, source_kind)
            VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, src_id, dst_id, predicate)
            DO UPDATE SET weight = kg_edges.weight + 1
            """,
            (user_id, src_id, dst_id, predicate, doc_id, chunk_index, evidence, source_kind),
        )


def find_kg_node_ids(user_id, lemmas, doc_ids=None, limit=8):
    """Nodes whose lemmas overlap the given ones, rarest match first.

    ``doc_ids`` restricts the answer to nodes mentioned inside those documents,
    which is how a share recipient and a request naming specific documents stay
    inside their scope.
    """
    if not lemmas:
        return []
    params = [list(lemmas), user_id, list(lemmas)]
    scope_sql = ""
    if doc_ids is not None:
        scope_sql = (
            " AND EXISTS (SELECT 1 FROM kg_occurrences o"
            " WHERE o.node_id = n.id AND o.user_id = n.user_id AND o.doc_id = ANY(%s::text[]))"
        )
        params.append(list(doc_ids))
    params.append(limit)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            f"""
            SELECT n.id,
                   cardinality(ARRAY(SELECT unnest(n.lemmas) INTERSECT SELECT unnest(%s::text[]))) AS matched
            FROM kg_nodes n
            WHERE n.user_id = %s AND n.lemmas && %s::text[]{scope_sql}
            ORDER BY matched DESC, n.name
            LIMIT %s
            """,
            tuple(params),
        )
        return [(str(row["id"]), int(row["matched"])) for row in c.fetchall()]


def get_kg_node_ids_for_chunks(user_id, refs):
    """Nodes mentioned in the given ``(doc_id, chunk_index)`` pairs."""
    if not refs:
        return []
    doc_ids = sorted({doc_id for doc_id, _ in refs})
    wanted = set(refs)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT node_id, doc_id, chunk_index FROM kg_occurrences"
            " WHERE user_id = %s AND doc_id = ANY(%s::text[])",
            (user_id, doc_ids),
        )
        found = []
        for row in c.fetchall():
            if (row["doc_id"], row["chunk_index"]) in wanted:
                node_id = str(row["node_id"])
                if node_id not in found:
                    found.append(node_id)
        return found


def get_kg_edges(user_id, node_ids, hops=2, doc_ids=None):
    """Breadth-first edges around ``node_ids``, scoped to ``doc_ids``.

    Each returned edge carries the distance at which it was reached, so the
    caller can weigh a direct relation above a two-hop one.
    """
    if not node_ids or hops < 1:
        return []
    visited = {str(node_id) for node_id in node_ids}
    frontier = list(visited)
    edges = []
    seen_ids = set()
    with get_db() as conn:
        c = conn.cursor()
        for distance in range(1, hops + 1):
            if not frontier:
                break
            params = [user_id, frontier, frontier]
            scope_sql = ""
            if doc_ids is not None:
                scope_sql = " AND doc_id = ANY(%s::text[])"
                params.append(list(doc_ids))
            c.execute(
                f"""
                SELECT id, src_id, dst_id, predicate, weight, doc_id, chunk_index, evidence
                FROM kg_edges
                WHERE user_id = %s AND (src_id = ANY(%s::uuid[]) OR dst_id = ANY(%s::uuid[])){scope_sql}
                """,
                tuple(params),
            )
            next_frontier = []
            for row in c.fetchall():
                if row["id"] in seen_ids:
                    continue
                seen_ids.add(row["id"])
                edges.append(
                    {
                        "id": row["id"],
                        "src_id": str(row["src_id"]),
                        "dst_id": str(row["dst_id"]),
                        "predicate": row["predicate"],
                        "weight": int(row["weight"]),
                        "doc_id": row["doc_id"],
                        "chunk_index": row["chunk_index"],
                        "evidence": row["evidence"],
                        "distance": distance,
                    }
                )
                for endpoint in (str(row["src_id"]), str(row["dst_id"])):
                    if endpoint not in visited:
                        visited.add(endpoint)
                        next_frontier.append(endpoint)
            frontier = next_frontier
    return edges


def _delete_kg_rows(cursor, doc_id, user_id):
    """Remove every graph row derived from one document, then orphan nodes.

    Shared by ``delete_kg_document`` and ``delete_document`` so the cascade
    runs inside the caller's transaction when there is one.
    """
    cursor.execute("DELETE FROM kg_occurrences WHERE doc_id = %s AND user_id = %s", (doc_id, user_id))
    cursor.execute("DELETE FROM kg_edges WHERE doc_id = %s AND user_id = %s", (doc_id, user_id))
    cursor.execute("DELETE FROM kg_doc_vectors WHERE doc_id = %s AND user_id = %s", (doc_id, user_id))
    cursor.execute("DELETE FROM kg_build_state WHERE doc_id = %s AND user_id = %s", (doc_id, user_id))
    cursor.execute(
        "DELETE FROM kg_doc_edges WHERE user_id = %s AND (src_doc_id = %s OR dst_doc_id = %s)",
        (user_id, doc_id, doc_id),
    )
    cursor.execute(
        "DELETE FROM kg_nodes n WHERE n.user_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM kg_occurrences o WHERE o.node_id = n.id)",
        (user_id,),
    )


def delete_kg_document(doc_id, user_id):
    """Drop every graph row derived from one document, then orphan nodes."""
    with get_db() as conn:
        _delete_kg_rows(conn.cursor(), doc_id, user_id)


def get_kg_build_state(doc_id, user_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM kg_build_state WHERE doc_id = %s AND user_id = %s", (doc_id, user_id))
        row = c.fetchone()
        return dict(row) if row else None


def set_kg_build_state(doc_id, user_id, status, chunks_total=0, chunks_done=0, model="", error=""):
    with get_db() as conn:
        conn.cursor().execute(
            """
            INSERT INTO kg_build_state (doc_id, user_id, status, chunks_total, chunks_done, model, error, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (doc_id) DO UPDATE
            SET status = EXCLUDED.status, chunks_total = EXCLUDED.chunks_total,
                chunks_done = EXCLUDED.chunks_done, model = EXCLUDED.model,
                error = EXCLUDED.error, updated_at = CURRENT_TIMESTAMP
            """,
            (doc_id, user_id, status, chunks_total, chunks_done, model, error),
        )


def kg_status(user_id):
    """Counters the UI and the CLI print."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT
                (SELECT count(*) FROM kg_build_state WHERE user_id = %s) AS documents,
                (SELECT coalesce(sum(chunks_total), 0) FROM kg_build_state WHERE user_id = %s) AS chunks_total,
                (SELECT coalesce(sum(chunks_done), 0) FROM kg_build_state WHERE user_id = %s) AS chunks_done,
                (SELECT count(*) FROM kg_nodes WHERE user_id = %s) AS nodes,
                (SELECT count(*) FROM kg_edges WHERE user_id = %s) AS edges
            """,
            (user_id,) * 5,
        )
        row = dict(c.fetchone())
    row["ready"] = bool(row["nodes"] and row["chunks_done"] >= row["chunks_total"] > 0)
    return row


def replace_kg_doc_edges(user_id, edges):
    """Swap the whole document layer of one account for ``edges``."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM kg_doc_edges WHERE user_id = %s", (user_id,))
        if edges:
            c.executemany(
                "INSERT INTO kg_doc_edges (user_id, src_doc_id, dst_doc_id, score) VALUES (%s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                [(user_id, src, dst, float(score)) for src, dst, score in edges],
            )
    return len(edges)


def get_kg_doc_edges(user_id, doc_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT dst_doc_id, score FROM kg_doc_edges WHERE user_id = %s AND src_doc_id = %s"
            " ORDER BY score DESC",
            (user_id, doc_id),
        )
        return [(row["dst_doc_id"], float(row["score"])) for row in c.fetchall()]


def get_rag_chunk(user_id, doc_id, chunk_index):
    """One indexed chunk of a document, or ``None`` when it does not exist."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT text FROM rag_chunks WHERE user_id = %s AND doc_id = %s AND chunk_index = %s",
            (user_id, doc_id, chunk_index),
        )
        row = c.fetchone()
        return row["text"] if row else None


def get_rag_chunks(user_id, refs):
    """Chunk texts and file names for many ``(doc_id, chunk_index)`` at once.

    The graph stage reads the chunks behind a dozen edges; one connection per
    chunk was a dozen short-lived connections inside a single search.
    """
    if not refs:
        return {}
    wanted = {(str(doc_id), int(index)) for doc_id, index in refs}
    doc_ids = sorted({doc_id for doc_id, _ in wanted})
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT doc_id, chunk_index, text, filename FROM rag_chunks"
            " WHERE user_id = %s AND doc_id = ANY(%s::text[])",
            (user_id, doc_ids),
        )
        return {
            (row["doc_id"], int(row["chunk_index"])): (row["text"], row["filename"] or "")
            for row in c.fetchall()
            if (row["doc_id"], int(row["chunk_index"])) in wanted
        }


def count_rag_chunks(user_id, doc_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT count(*) AS n FROM rag_chunks WHERE user_id = %s AND doc_id = %s", (user_id, doc_id))
        return int(c.fetchone()["n"])


def get_kg_node_names(user_id, node_ids):
    """Display names of graph nodes, keyed by id."""
    if not node_ids:
        return {}
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, name FROM kg_nodes WHERE user_id = %s AND id = ANY(%s::uuid[])",
            (user_id, [str(node_id) for node_id in node_ids]),
        )
        return {str(row["id"]): row["name"] for row in c.fetchall()}


def get_document_filenames(user_id, doc_ids):
    """File names of documents, keyed by id (used to cite graph evidence)."""
    if not doc_ids:
        return {}
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, filename FROM documents WHERE user_id = %s AND id = ANY(%s::text[])", (user_id, list(doc_ids)))
        return {row["id"]: row["filename"] for row in c.fetchall()}


def get_kg_graph(user_id, doc_id="", kind="", limit=400, doc_ids=None):
    """Nodes and the edges among them, for the graph view."""
    limit = max(1, int(limit))
    params: list = [user_id]
    filters = ""
    if kind:
        filters += " AND n.kind = %s"
        params.append(kind)
    if doc_id:
        filters += (
            " AND EXISTS (SELECT 1 FROM kg_occurrences o"
            " WHERE o.node_id = n.id AND o.user_id = n.user_id AND o.doc_id = %s)"
        )
        params.append(doc_id)
    elif doc_ids is not None:
        filters += (
            " AND EXISTS (SELECT 1 FROM kg_occurrences o"
            " WHERE o.node_id = n.id AND o.user_id = n.user_id AND o.doc_id = ANY(%s::text[]))"
        )
        params.append(list(doc_ids))
    params.append(limit)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            f"""
            SELECT n.id, n.name, n.kind, n.description,
                   (SELECT count(*) FROM kg_occurrences o WHERE o.node_id = n.id) AS mentions
            FROM kg_nodes n
            WHERE n.user_id = %s{filters}
            ORDER BY mentions DESC, n.name
            LIMIT %s
            """,
            tuple(params),
        )
        nodes = [
            {
                "id": str(row["id"]),
                "label": row["name"],
                "kind": row["kind"],
                "description": row["description"] or "",
                "mentions": int(row["mentions"] or 0),
            }
            for row in c.fetchall()
        ]
        if not nodes:
            return {"nodes": [], "edges": []}
        ids = [node["id"] for node in nodes]
        # Nodes are capped by the caller; edges need a cap of their own or a
        # dense account returns tens of thousands of links in one response.
        c.execute(
            "SELECT src_id, dst_id, predicate, weight, doc_id FROM kg_edges"
            " WHERE user_id = %s AND src_id = ANY(%s::uuid[]) AND dst_id = ANY(%s::uuid[])"
            " ORDER BY weight DESC LIMIT %s",
            (user_id, ids, ids, max(1, limit * 4)),
        )
        edges = [
            {
                "source": str(row["src_id"]),
                "target": str(row["dst_id"]),
                "predicate": row["predicate"],
                "weight": int(row["weight"] or 1),
                "doc_id": row["doc_id"],
            }
            for row in c.fetchall()
        ]
    return {"nodes": nodes, "edges": edges}


def get_kg_counts(user_id, doc_ids=None):
    """Total nodes and edges in scope, not just the ones a page returns."""
    params: list = [user_id]
    scope_sql = ""
    if doc_ids is not None:
        scope_sql = (
            " AND EXISTS (SELECT 1 FROM kg_occurrences o"
            " WHERE o.node_id = n.id AND o.user_id = n.user_id AND o.doc_id = ANY(%s::text[]))"
        )
        params.append(list(doc_ids))
    with get_db() as conn:
        c = conn.cursor()
        c.execute(f"SELECT count(*) AS n FROM kg_nodes n WHERE n.user_id = %s{scope_sql}", tuple(params))
        nodes = int(c.fetchone()["n"])
        c.execute("SELECT count(*) AS n FROM kg_edges WHERE user_id = %s", (user_id,))
        edges = int(c.fetchone()["n"])
    return {"nodes": nodes, "edges": edges}


def get_kg_document_graph(user_id, limit=400, doc_ids=None):
    """The document layer: indexed documents and their similarity edges."""
    limit = max(1, int(limit))
    params: list = [user_id]
    scope_sql = ""
    if doc_ids is not None:
        scope_sql = " AND id = ANY(%s::text[])"
        params.append(list(doc_ids))
    params.append(limit)
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, filename FROM documents WHERE user_id = %s AND index_status = 'indexed'"
            f"{scope_sql} ORDER BY filename LIMIT %s",
            tuple(params),
        )
        nodes = [
            {"id": row["id"], "label": row["filename"], "kind": "document", "description": "", "mentions": 0}
            for row in c.fetchall()
        ]
        if not nodes:
            return {"nodes": [], "edges": []}
        ids = [node["id"] for node in nodes]
        c.execute(
            "SELECT src_doc_id, dst_doc_id, score FROM kg_doc_edges"
            " WHERE user_id = %s AND src_doc_id = ANY(%s::text[]) AND dst_doc_id = ANY(%s::text[])",
            (user_id, ids, ids),
        )
        edges = [
            {
                "source": row["src_doc_id"],
                "target": row["dst_doc_id"],
                "predicate": "similar",
                "weight": round(float(row["score"]), 3),
                "doc_id": row["src_doc_id"],
            }
            for row in c.fetchall()
        ]
    return {"nodes": nodes, "edges": edges}
