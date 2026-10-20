"""Artifact inspectors: how a produced artifact is described for verification.

A step's result is judged by a text-only reasoning call, so a picture has to be
turned into text first. Which step types can be inspected, and how, is a
registry — adding a new artifact type (a video frame, audio, a document scan)
is one entry here, never a branch in the executor.
"""

import base64
import logging
import os
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Inspector = Callable[[Any, dict, str, str], tuple[str, float]]


def describe_image_artifact(app: Any, media: dict, lang: str, preview_data: str = "") -> tuple[str, float]:
    """Describe a produced image (or a video preview frame) as text.

    For a ``video/mp4`` without a poster frame the description is empty; when
    the wrapper provides a JPEG preview of the generated clip the frame is
    described instead. Returns ``(text, seconds)``; the text is "" for a
    non-image artifact without a preview, or an unavailable/empty description.
    """
    is_image = str(media.get("file_type") or "").startswith("image/")
    if not is_image and not preview_data:
        return "", 0.0
    file_name = str(media.get("file_name") or media.get("file_path", ""))
    mm = app.modules.get("multimodal")
    if mm is None or not getattr(mm, "available", False):
        return "", 0.0
    if is_image:
        rel = str(media.get("file_path") or "")
        upload_folder = str(app.config.get("UPLOAD_FOLDER") or "")
        if not rel or not upload_folder:
            return "", 0.0
        path = os.path.realpath(os.path.join(upload_folder, rel))
        if not path.startswith(os.path.realpath(upload_folder) + os.sep) or not os.path.isfile(path):
            return "", 0.0
        start = time.monotonic()
        try:
            with open(path, "rb") as fh:
                image_b64 = base64.b64encode(fh.read()).decode("ascii")
        except OSError as e:
            logger.warning(f"step image unread for the checkpoint ({rel}): {e}")
            return "", 0.0
        account_id = f"task-step-image:{os.path.basename(path)}"
        from_file = True
    else:
        image_b64 = preview_data
        account_id = f"task-step-video-frame:{file_name}"
        start = time.monotonic()
        from_file = False
    from app.utils import begin_usage_account, finish_usage_account

    begin_usage_account(account_id)
    try:
        description, _error = mm.describe_image_for_rlm(image_b64, lang=lang)
    finally:
        finish_usage_account(account_id)
    seconds = round(time.monotonic() - start, 2)
    if not description:
        return "", seconds
    from flask_babel import force_locale
    from flask_babel import gettext as _

    with force_locale(lang):
        if from_file:
            label = _("{file_name} — image description").format(file_name=file_name)
        else:
            label = _("{file_name} — frame from the generated video").format(file_name=file_name)
    return f"{label}\n{description}", seconds


ARTIFACT_INSPECTORS: dict[str, Inspector] = {
    "image_gen": describe_image_artifact,
    "image_edit": describe_image_artifact,
}


def get_inspector(step_type: str) -> Inspector | None:
    """The inspector registered for a step type, or None (not inspectable)."""
    return ARTIFACT_INSPECTORS.get(step_type)
