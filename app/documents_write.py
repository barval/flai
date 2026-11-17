"""Write a document the assistant produced, as an ordinary document of its user.

Everything that makes an upload safe applies here, and one thing more: the caller is a
model, so its name is a display name and nothing else. Nothing in this module accepts a
path, and every check runs before the first byte reaches the disk — a refusal must leave
no half-document behind.

Errors carry a code rather than a sentence: the tool layer turns the code into the user's
language, because this module has no request context of its own.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass

from flask import current_app

from app import db

logger = logging.getLogger(__name__)

MAX_CONTENT_BYTES = 256 * 1024
FORMAT_EXTENSIONS = {"md": ".md", "csv": ".csv", "json": ".json", "txt": ".txt"}
MAX_NAME_STEM = 120


class DocumentWriteError(ValueError):
    """A write that was refused; ``code`` is the reason, ``detail`` may name a value."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class WrittenDocument:
    id: str
    filename: str
    size: int
    file_path: str


def _documents_root() -> str:
    return current_app.config.get("DOCUMENTS_FOLDER", "data/documents")


def _display_name(name: str, extension: str) -> str:
    """A display name from whatever the model called it: no directory, no control bytes."""
    text = str(name or "").replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    stem, suffix = os.path.splitext(text)
    if suffix.lower() == extension:
        text, stem = stem, stem
    elif suffix and suffix.lower() in FORMAT_EXTENSIONS.values():
        stem = text
    else:
        text, stem = f"{text}{extension}", text
    stem = stem[:MAX_NAME_STEM].strip()
    if not stem:
        raise DocumentWriteError("name")
    return f"{stem}{extension}"


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


def _payload(content: str, fmt: str, rows: list[list[str]] | None) -> str:
    if fmt == "csv":
        if not rows:
            raise DocumentWriteError("empty")
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
) -> WrittenDocument:
    """Store one document. Raises ``DocumentWriteError`` before writing when refused."""
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

    data = payload.encode("utf-8")
    if len(data) > MAX_CONTENT_BYTES:
        raise DocumentWriteError("too_large", str(len(data)))
    if _storage_full(user_id, len(data)):
        raise DocumentWriteError("quota")

    filename = _free_name(user_id, _display_name(name, extension))

    doc_id = uuid.uuid4().hex
    folder = os.path.join(_documents_root(), user_id)
    relative = os.path.join(user_id, f"{doc_id}{extension}")
    absolute = os.path.join(_documents_root(), relative)
    root = os.path.realpath(_documents_root())
    if not os.path.realpath(absolute).startswith(root + os.sep):
        raise DocumentWriteError("name", filename)

    os.makedirs(folder, exist_ok=True)
    text = payload if payload.endswith("\n") else f"{payload}\n"
    try:
        with open(absolute, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        size = os.path.getsize(absolute)
        db.save_document(user_id, doc_id, filename, size, extension, relative)
        db.update_user_storage(user_id, size)
    except Exception:
        with open(absolute, "w", encoding="utf-8"):
            pass
        os.remove(absolute)
        raise

    logger.info(f"document written by the assistant: {filename} ({size} bytes) for {user_id!r}")
    return WrittenDocument(id=doc_id, filename=filename, size=size, file_path=relative)
