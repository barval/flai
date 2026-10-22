# app/tasks/answer_images.py
"""Background multimodal description of images attached to a task answer.

The answer route (`app/routes/tasks.py:task_answer`) saves the user's answer
and returns 202 immediately; this thread describes the attached images with
the multimodal model afterwards and appends the descriptions to the
``user_input`` step's result. The next reasoning step reads the enriched
text from the step row (the executor is gated by the chat-activity hook, so
it may already consume a not-yet-described answer — the description then
arrives while the reasoning call is still streaming its first steps; to
avoid a race the description is fed to the step BEFORE the executor picks
the task up: the thread holds a short DB lock per append).

Race note: the executor may resume the task while the description is still
running. ``add_user_input_step`` leaves the row ``pending``, and the
executor consumes unprocessed user_input rows in ``_process_user_answer``
— so the description thread must finish BEFORE the executor reads the row.
The answer route therefore marks the task ``frozen`` (reason ``answer_images``)
while the thread runs and restores ``awaiting_user`` when it is done; the
executor's selection loop skips frozen tasks (same mechanism a chat abort
uses), so no task can be resumed mid-description. The audio-transcription
thread (``app/tasks/audio_transcribe.py``) shares that freeze when the same
answer carries audio: the last worker to finish restores the state.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

_DESCRIBE_THREADS_LOCK = threading.Lock()
_ACTIVE: set[str] = set()


def is_describing(task_id: str) -> bool:
    """True while the image-description thread of *task_id* is running.

    Public because the audio-transcription thread shares the same freeze: an
    answer with images AND audio must not be resumed by whichever worker
    finishes first (``app/tasks/audio_transcribe.py``).
    """
    with _DESCRIBE_THREADS_LOCK:
        return task_id in _ACTIVE


def describe_answer_images(app, task: dict, step_id: str, images: list[dict[str, str]]) -> None:
    """Spawn the background description thread (one per task, de-duplicated).

    Returns immediately: the HTTP response is not held for GPU work.
    """

    def _worker() -> None:
        from app.tasks.store import get_step, get_task, update_step, update_task

        # Freeze the task while describing: the executor's selection loop
        # skips frozen tasks, so it cannot consume the pending user_input
        # row before the descriptions are appended.

        task_id = task["id"]
        update_task(task_id, status="frozen", abort_reason="answer_images")
        try:
            lang = task.get("lang") or "ru"
            from app.utils import begin_usage_account, finish_usage_account

            acc_id = f"task-ans-image:{task_id}"
            begin_usage_account(acc_id)
            upload_folder = str(app.config.get("UPLOAD_FOLDER") or "")
            mm = app.modules.get("multimodal")
            extra_text = ""
            usage_totals: dict[str, int] = {}
            if mm and mm.available:
                for img in images:
                    file_path = img.get("file_path") or ""
                    if not file_path:
                        continue
                    full_path = os.path.join(upload_folder, file_path)
                    if not os.path.isfile(full_path):
                        continue
                    import base64 as _b64

                    with open(full_path, "rb") as fh:
                        b64 = _b64.b64encode(fh.read()).decode("ascii")
                    desc, _err = mm.describe_image_for_rlm(b64, lang=lang)
                    if desc:
                        from flask_babel import force_locale
                        from flask_babel import gettext as _

                        with force_locale(lang):
                            label = _("{file_name} — image description").format(file_name=img.get("file_name", ""))
                        extra_text += f"\n\n[{label}]\n{desc}"
                usage = finish_usage_account(acc_id) or {}
                usage_totals = {k: int(v) for k, v in usage.items() if isinstance(v, (int, float)) and v > 0}
            else:
                finish_usage_account(acc_id)

            # Re-read the step (the answer may have been updated) and append.
            row = get_step(step_id)
            if row is None:
                return
            current = str(row.get("result") or "")
            new_result = current + extra_text if extra_text else current
            extra = dict(row.get("extra") or {})
            if usage_totals:
                extra["usage"] = usage_totals
            update_step(step_id, result=new_result, extra=extra, status="pending")
        except Exception as e:  # noqa: BLE001 — the description is best effort
            logger.warning(f"answer image description failed for task {task['id']}: {e}")
        finally:
            # Restore the awaiting state: the executor's selection loop picks
            # the task up again (the user_input row is still pending). The
            # audio-transcription thread may hold the same freeze (an answer
            # with images AND audio): then it restores the state instead.
            from app.tasks.audio_transcribe import is_transcribing
            from app.tasks.events import publish_task_update

            current = get_task(task_id)
            if (
                current is not None
                and current.get("status") == "frozen"
                and current.get("abort_reason") == "answer_images"
                and not is_transcribing(task_id)
            ):
                update_task(task_id, status="awaiting_user", abort_reason=None)
            publish_task_update(app, get_task(task_id))
            with _DESCRIBE_THREADS_LOCK:
                _ACTIVE.discard(task_id)

    thread = threading.Thread(target=_worker, name=f"answer-images-{task['id'][:8]}", daemon=True)
    with _DESCRIBE_THREADS_LOCK:
        _ACTIVE.add(task["id"])
    thread.start()
