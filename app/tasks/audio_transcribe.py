# app/tasks/audio_transcribe.py
"""Background Whisper transcription of audio attached to a task.

Chat parity (v12.6): a recorded voice message or an audio file attached to a
task request or answer is transcribed into text and fed to the task — the
transcript is CONTEXT (like an image description), never a Documents-panel
document and never indexed.

The route saves the audio and returns 202 immediately; this thread transcribes
it with ``AudioModule.transcribe`` — the same HTTP call to the Whisper sidecar
the chat's ``transcribe_audio`` fast-worker task makes, so no GPU lock is held
— and writes each transcript into its ``tasks.audio`` entry.

Where the transcript lands (and why the task waits):

* request phase — the executor's request accessor (``executor._request_text``)
  appends the request transcripts for the planner AND every step, so a task
  dictated by voice is planned from the dictated text, and asking to analyze
  the recording works. A dictated task also takes its title from the
  transcript when the request carried no text.
* answer phase — the transcript is appended to the ``user_input`` step the
  answer created (the ``answer_images`` contract).

The task is ``frozen`` (reason ``audio_transcript``) while the thread runs: the
executor's ``_run_task`` skips it, so the planner can never start from a
request whose dictated text has not arrived yet. The thread restores the
working state when it is done (``queued`` for a request, ``awaiting_user`` for
an answer) — unless the image-description thread still holds the freeze, in
which case that one restores it.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# The freeze reason the route sets and the executor skips.
ABORT_REASON = "audio_transcript"
_ANSWER_PHASE = "answer"

_THREADS_LOCK = threading.Lock()
_ACTIVE: set[str] = set()


def is_transcribing(task_id: str) -> bool:
    """True while the transcription thread of *task_id* is running."""
    with _THREADS_LOCK:
        return task_id in _ACTIVE


def start_task_audio_transcription(app: Any, task_id: str) -> None:
    """Spawn the background transcription thread (one per task, de-duplicated).

    Returns immediately: the HTTP response is not held for Whisper.
    """

    def _worker() -> None:
        try:
            transcribe_task_audio(app, task_id)
        finally:
            with _THREADS_LOCK:
                _ACTIVE.discard(task_id)

    with _THREADS_LOCK:
        if task_id in _ACTIVE:
            return
        _ACTIVE.add(task_id)
    threading.Thread(target=_worker, name=f"task-audio-{task_id[:8]}", daemon=True).start()


def transcribe_task_audio(app: Any, task_id: str) -> None:
    """Transcribe the task's pending audio and feed the transcripts into it.

    The core of the background thread (tests call it directly).
    """
    from app.tasks.store import get_task, update_step, update_task

    task = get_task(task_id)
    if task is None:
        return
    entries = [dict(e) for e in (task.get("audio") or []) if isinstance(e, dict)]
    lang = str(task.get("lang") or "ru")
    pending = [e for e in entries if not _text(e.get("transcript"))]
    if pending:
        audio_module = (app.modules or {}).get("audio")
        upload_folder = str(app.config.get("UPLOAD_FOLDER") or "")
        for entry in pending:
            entry["transcript"] = _transcribe_entry(audio_module, upload_folder, entry, lang)
        update_task(task_id, audio=entries)
        if not any(_text(e.get("transcript")) for e in entries):
            # Nothing to work with: a silent empty task is worse than a clear
            # localized failure the user can retry.
            _fail_task(task_id, lang)
            _publish(app, task_id)
            return
        transcribed = [e for e in pending if _text(e.get("transcript"))]
        title = _dictated_title(task, transcribed)
        if title:
            update_task(task_id, title=title)
        _append_answer_transcripts(transcribed, lang, update_step)
    _restore_status(task_id, entries)
    _publish(app, task_id)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _transcribe_entry(audio_module: Any, upload_folder: str, entry: dict, lang: str) -> str:
    """Whisper transcript of one attachment ("" when unavailable or unreadable)."""
    if audio_module is None:
        return ""
    path = os.path.join(upload_folder, str(entry.get("file_path") or ""))
    if not os.path.isfile(path):
        logger.warning(f"task audio file missing: {path}")
        return ""
    try:
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
        text = audio_module.transcribe(
            data,
            str(entry.get("file_type") or "audio/webm"),
            str(entry.get("file_name") or "audio"),
            lang=lang,
        )
    except Exception as e:  # noqa: BLE001 — a broken sidecar must not kill the thread
        logger.warning(f"task audio transcription failed for {entry.get('file_name')}: {e}")
        return ""
    return _text(text)


def _dictated_title(task: dict, transcribed: list[dict]) -> str:
    """The transcript names a task that was dictated (its request had no text)."""
    if _text(task.get("title")):
        return ""
    for entry in transcribed:
        if str(entry.get("phase") or "request") != _ANSWER_PHASE:
            text = _text(entry.get("transcript"))
            if text:
                return text[:300]
    return ""


def _append_answer_transcripts(transcribed: list[dict], lang: str, update_step: Any) -> None:
    """Append an answer's transcript to the ``user_input`` step that holds it."""
    from flask_babel import force_locale
    from flask_babel import gettext as _

    from app.tasks.store import get_step

    for entry in transcribed:
        if str(entry.get("phase") or "") != _ANSWER_PHASE:
            continue
        step_id = _text(entry.get("step_id"))
        text = _text(entry.get("transcript"))
        if not step_id or not text:
            continue
        row = get_step(step_id)
        if row is None:
            continue
        with force_locale(lang):
            label = "🎤 " + _("Transcribed") + f" ({entry.get('file_name') or ''}):"
        block = f"[{label}]\n{text}"
        current = _text(row.get("result"))
        update_step(step_id, result=f"{current}\n\n{block}" if current else block, status="pending")


def _restore_status(task_id: str, entries: list[dict]) -> None:
    """Return the task to its working state once the attachment work is done.

    ``answer_images`` may hold the same freeze (images AND audio on one
    answer): whichever thread finishes last restores the state, so the executor
    can never consume a half-enriched answer.
    """
    from app.tasks.answer_images import is_describing
    from app.tasks.store import get_task, update_task

    if is_describing(task_id):
        return
    current = get_task(task_id)
    if current is None or current.get("status") != "frozen" or current.get("abort_reason") != ABORT_REASON:
        # Terminal (stopped/deleted) or already resumed — never flip it back.
        return
    answered = any(str(e.get("phase") or "") == _ANSWER_PHASE for e in entries)
    update_task(task_id, status="awaiting_user" if answered else "queued", abort_reason=None)


def _fail_task(task_id: str, lang: str) -> None:
    """Terminal failure with a localized ⚠️ error step (executor parity)."""
    from app.llamacpp_client import _tr
    from app.tasks.executor import add_final_step
    from app.tasks.store import update_task

    add_final_step(task_id, f"⚠️ {_tr('Failed to recognize speech', lang=lang)}", status="failed")
    update_task(task_id, status="failed", completed_at=datetime.now(UTC).isoformat(), abort_reason=None)
    logger.error(f"task {task_id} failed: no audio transcript")


def _publish(app: Any, task_id: str) -> None:
    from app.tasks.events import publish_task_update
    from app.tasks.store import get_task

    publish_task_update(app, get_task(task_id))
