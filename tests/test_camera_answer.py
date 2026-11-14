"""A classifier marker must never reach the chat as the camera's answer."""

from __future__ import annotations

from unittest.mock import Mock

from app.queue import RedisRequestQueue


def _queue() -> Mock:
    queue = Mock()
    queue.app = Mock()
    queue.app.config = {"UPLOAD_FOLDER": "/tmp/uploads", "CAMERA_ANALYSIS_WIDTH": 1024}
    queue.logger = Mock()
    queue._get_model_name = Mock(return_value="Qwen3.6-35B")
    queue._save_camera_message = Mock(return_value={"message_id": 1})
    queue._save_and_respond = Mock(return_value={"message_id": 2})
    queue._process_camera_task = lambda *args, **kwargs: RedisRequestQueue._process_camera_task(
        queue, *args, **kwargs
    )
    return queue


def test_a_marker_reply_is_retried_and_never_saved(monkeypatch):
    queue = _queue()

    cam = Mock()
    cam.available = True
    cam.get_snapshot.return_value = {
        "success": True,
        "room_name": "кабинет",
        "image_data": "AAAA",
        "image_type": "image/jpeg",
        "file_name": "snap.jpg",
        "file_size": 3,
    }

    multimodal = Mock()
    multimodal.available = True
    multimodal.process_image_with_text.side_effect = [
        ("[-IMAGE-EDIT-] Как дела в кабинете", None),
        ("В кабинете пусто, свет выключен.", None),
    ]

    base = Mock()
    base._ = lambda text, lang=None: "Describe what the camera snapshot of {room_name} shows."

    queue.app.modules = {"cam": cam, "multimodal": multimodal, "base": base}

    result = queue._process_camera_task("kab", "session", "user", "Как дела в кабинете", "now", "ru")

    assert result["response"] == "В кабинете пусто, свет выключен."
    assert "[-IMAGE-EDIT-]" not in (result["response"] or "")
    assert multimodal.process_image_with_text.call_count == 2, "a marker must trigger one retry"
    for call in multimodal.process_image_with_text.call_args_list:
        assert call.kwargs.get("mode") == "camera"


def test_a_marker_twice_becomes_an_error_not_the_marker(monkeypatch):
    queue = _queue()

    cam = Mock()
    cam.available = True
    cam.get_snapshot.return_value = {"success": True, "room_name": "кабинет", "image_data": "AAAA"}

    multimodal = Mock()
    multimodal.available = True
    multimodal.process_image_with_text.return_value = ("[-IMAGE-EDIT-] Как дела в кабинете", None)

    base = Mock()
    base._ = lambda text, lang=None: "Describe what the camera snapshot of {room_name} shows."

    queue.app.modules = {"cam": cam, "multimodal": multimodal, "base": base}

    result = queue._process_camera_task("kab", "session", "user", "Как дела в кабинете", "now", "ru")

    assert "[-IMAGE-EDIT-]" not in (result["response"] or "")
    assert result["response"].startswith("⚠️ ")
