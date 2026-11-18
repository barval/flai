"""Write a document the assistant produced, as an ordinary document of its user.

Everything that makes an upload safe applies here, and one thing more: the caller is a
model, so its name is a display name and nothing else. Nothing in this module accepts a
path, and every check runs before the first byte reaches the disk — a refusal must leave
no half-document behind.

Errors carry a code rather than a sentence: the tool layer turns the code into the user's
language, because this module has no request context of its own.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import logging
import os
import re
import threading
import uuid
from dataclasses import dataclass

from flask import current_app

from app import db

logger = logging.getLogger(__name__)

MAX_CONTENT_BYTES = 256 * 1024
# A log may grow past one call's payload, but not without end: an unbounded file inside
# the document store is a disk leak with a friendly name.
MAX_APPEND_BYTES = 2 * 1024 * 1024
FORMAT_EXTENSIONS = {"md": ".md", "csv": ".csv", "json": ".json", "txt": ".txt"}
MAX_NAME_STEM = 120

# Name allocation is a read-then-write: two chats can both see "note.md" free. One
# process runs the chat loop, so a lock covers it; a second worker would need a
# database constraint instead.
_write_lock = threading.Lock()


class DocumentWriteError(ValueError):
    """A write that was refused; ``code`` is the reason, ``detail`` may name a value."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class WrittenDocument:
    id: str
    filename: str
    size: int
    file_path: str
    absolute_path: str


def _documents_root() -> str:
    return current_app.config.get("DOCUMENTS_FOLDER", "data/documents")


def _display_name(name: str, extension: str) -> str:
    """A display name from whatever the model called it: no directory, no control bytes.

    A suffix the caller wrote ("report.pdf" asked for as markdown) is replaced rather
    than doubled; a name without one gets the format's.
    """
    text = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    stem, suffix = os.path.splitext(text)
    if not suffix or not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", suffix):
        stem = text
    stem = stem[:MAX_NAME_STEM].strip()
    if not stem:
        raise DocumentWriteError("name")
    return f"{stem}{extension}"


def _append_target(user_id: str, name: str, extension: str):
    """The generated document an append may extend, or None when it does not exist.

    Only a document this tool created is eligible: an upload's bytes are shared through
    the content-addressed blob store, so appending to a same-named upload would edit a
    file another user also references.
    """
    wanted = _display_name(name, extension)
    for doc in db.get_user_documents(user_id):
        if doc.get("filename") != wanted:
            continue
        # `source` is the whole guard: a generated document never registers a blob, and
        # the column is not even selected for the panel's list (see docs/DOCUMENTS.md).
        if doc.get("source") != "generated":
            raise DocumentWriteError("append_target", wanted)
        return doc
    return None


def _refuse_uploaded_name(user_id: str, filename: str) -> None:
    """An upload keeps its name: the assistant writes under a different one.

    Names are first come, first served. A generated document that collides with
    another generated one gets a suffix, but a name an upload already holds is the
    user's file, and silently writing "peoples (2)" next to it would hide the conflict
    from the model that asked for it.
    """
    for doc in db.get_user_documents(user_id):
        if doc.get("filename") == filename and doc.get("source") != "generated":
            raise DocumentWriteError("name_taken", filename)


def _free_name(user_id: str, filename: str) -> str:
    """The name the user does not already have; a generated file never overwrites."""
    taken = {doc.get("filename") for doc in db.get_user_documents(user_id)}
    if filename not in taken:
        return filename
    stem, suffix = os.path.splitext(filename)
    for index in range(2, 100):
        candidate = f"{stem} ({index}){suffix}"
        if candidate not in taken:
            return candidate
    raise DocumentWriteError("name", filename)


def _storage_full(user_id: str, size: int) -> bool:
    """Whether this many more bytes would cross the user's document quota."""
    used = db.get_user_storage_usage(user_id)
    limit = db.get_user_storage_limit(user_id)
    if limit is None:
        limit = int(current_app.config.get("MAX_DOCUMENTS_STORAGE_MB", 500)) * 1024 * 1024
    return used + size > limit


def count_quota_exceeded(user_id: str) -> bool:
    """The document COUNT limit, which the byte limit does not cover.

    Without it a model looping across turns could create thousands of tiny files and
    queue a GPU indexing job for each, all inside the byte budget.
    """
    from app.utils import check_document_quota

    # The language is explicit: without one the helper calls gettext(), whose locale
    # selector reads the request, and its own broad except would report "no quota"
    # instead of checking. Only None-vs-message matters here.
    try:
        return check_document_quota(user_id, lang="en") is not None
    except Exception:  # noqa: BLE001 - an unreadable quota is not a refusal
        return False


def _payload(content: str, fmt: str, rows: list[list[str]] | None) -> str:
    if fmt == "csv":
        if not rows:
            # A model that passes content instead of rows gets told which one is needed.
            raise DocumentWriteError("csv_rows" if str(content or "").strip() else "empty")
        if not isinstance(rows, list) or any(not isinstance(row, (list, tuple)) for row in rows):
            raise DocumentWriteError("csv_rows")
        buffer = io.StringIO()
        csv.writer(buffer).writerows(rows)
        return buffer.getvalue()
    if fmt == "json":
        json.loads(content)  # a generated file that does not parse is worse than none
        return content
    return content


def write_document_file(
    user_id: str,
    name: str,
    content: str = "",
    fmt: str = "md",
    rows: list[list[str]] | None = None,
    append: bool = False,
) -> WrittenDocument:
    """Store one document, or extend one this tool wrote. Refuses before writing."""
    format_key = str(fmt or "md").strip().lower().lstrip(".")
    if format_key not in FORMAT_EXTENSIONS:
        raise DocumentWriteError("format", format_key)
    extension = FORMAT_EXTENSIONS[format_key]

    try:
        payload = _payload(content, format_key, rows)
    except json.JSONDecodeError as exc:
        raise DocumentWriteError("json", str(exc)) from exc
    if not str(payload or "").strip():
        raise DocumentWriteError("empty")
    if not payload.endswith("\n"):
        payload = f"{payload}\n"

    data = payload.encode("utf-8")
    if len(data) > MAX_CONTENT_BYTES:
        raise DocumentWriteError("too_large", str(len(data)))

    root = os.path.realpath(_documents_root())
    folder = os.path.join(root, user_id)

    # The lock covers the name choice and the row: two chats can otherwise both see
    # "note.md" free and both keep that name.
    with _write_lock:
        existing = _append_target(user_id, name, extension) if append else None

        if existing is not None:
            absolute = os.path.join(root, existing["file_path"])
            if not os.path.realpath(absolute).startswith(root + os.sep) or not os.path.isfile(absolute):
                raise DocumentWriteError("append_target", existing["filename"])
            grown = os.path.getsize(absolute) + len(data)
            if grown > MAX_APPEND_BYTES:
                raise DocumentWriteError("too_large", str(grown))
            if _storage_full(user_id, len(data)):
                raise DocumentWriteError("quota")
            size_before = os.path.getsize(absolute)
            try:
                with open(absolute, "a", encoding="utf-8", newline="") as handle:
                    handle.write(payload)
                size = os.path.getsize(absolute)
                db.update_document_size(existing["id"], size)
                db.update_user_storage(user_id, len(data))
                if existing.get("index_status") in (db.INDEX_STATUS_INDEXED, db.INDEX_STATUS_INDEXING):
                    db.update_document_index_status(existing["id"], db.INDEX_STATUS_STALE)
            except Exception:
                # The bytes are on disk but the row is not: undo both sides, so the file
                # and the row that describes it never disagree about its size.
                with contextlib.suppress(OSError):
                    os.truncate(absolute, size_before)
                with contextlib.suppress(Exception):
                    db.update_document_size(existing["id"], size_before)
                raise
            logger.info(f"document appended by the assistant: {existing['filename']} (+{len(data)} bytes)")
            return WrittenDocument(
                id=existing["id"],
                filename=existing["filename"],
                size=size,
                file_path=existing["file_path"],
                absolute_path=absolute,
            )

        if _storage_full(user_id, len(data)) or count_quota_exceeded(user_id):
            raise DocumentWriteError("quota")

        wanted = _display_name(name, extension)
        _refuse_uploaded_name(user_id, wanted)
        filename = _free_name(user_id, wanted)
        doc_id = uuid.uuid4().hex
        relative = os.path.join(user_id, f"{doc_id}{extension}")
        absolute = os.path.join(root, relative)
        if not os.path.realpath(absolute).startswith(root + os.sep):
            raise DocumentWriteError("name", filename)

        os.makedirs(folder, exist_ok=True)
        try:
            with open(absolute, "w", encoding="utf-8", newline="") as handle:
                handle.write(payload)
            size = os.path.getsize(absolute)
            db.save_document(user_id, doc_id, filename, size, extension, relative, source="generated")
            db.update_document_index_status(doc_id, db.INDEX_STATUS_NOT_INDEXED)
            db.update_user_storage(user_id, size)
        except Exception:
            # Never let the cleanup replace the error that caused it, and never leave
            # a half-document behind.
            with contextlib.suppress(OSError):
                os.remove(absolute)
            raise

    logger.info(f"document written by the assistant: {filename} ({size} bytes) for {user_id!r}")
    return WrittenDocument(id=doc_id, filename=filename, size=size, file_path=relative, absolute_path=absolute)
