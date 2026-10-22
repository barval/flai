# tests/test_tasks_camera_subtask.py
"""Camera/vision sub-task handlers of the Tasks subsystem (v12.6).

The snapshot itself must ride the result envelope as media refs (chat parity:
the task view renders the image), the multimodal question must be framed as
analysis (a raw planner goal like "Take a snapshot…" misroutes the image
classifier to a router marker), and router markers echoed by the model must
never reach the step text.
"""

from unittest.mock import Mock, patch

import pytest


@pytest.mark.unit
class TestCameraSubtask:
    """_process_camera_subtask: framing, marker guard, retry, media refs."""

    @pytest.fixture(autouse=True)
    def _no_workers(self):
        with patch("app.queue.RedisRequestQueue.start_worker"):
            yield

    def _make_queue(self, multimodal, camera_result):
        from app.queue import RedisRequestQueue

        app = Mock()
        app.config = {
            "REDIS_URL": "redis://localhost:6379/0",
            "SECRET_KEY": "test-secret-key",
            "UPLOAD_FOLDER": "/tmp/uploads-test",
        }
        app.logger = Mock()
        base = Mock()
        base._ = lambda message, lang="en", **kw: message
        app.modules = {"base": base, "multimodal": multimodal, "cam": _cam(camera_result)}
        redis = Mock()
        redis.blpop.return_value = None
        redis.llen.return_value = 0
        redis.scard.return_value = 0
        pipe = Mock()
        pipe.execute.return_value = []
        redis.pipeline.return_value = pipe
        with patch("app.queue.redis.from_url", return_value=redis):
            queue = RedisRequestQueue(app)
        queue._publish_stream_event = Mock()
        queue._publish_result_event = Mock()
        return queue

    def _task(self):
        return {
            "id": "63f" + "0" * 29,  # UUID-shaped id for save_uploaded_file
            "data": {
                "room": "Сделайте снимок кухни и проверьте наличие кошки.",
                "prompt": "Сделайте снимок кухни и проверьте наличие кошки.",
                "task_question": "Найди по камерам, в какой комнате кошка.",
            },
            "session_id": "task:t1",
            "user_id": "tester",
            "lang": "en",
        }

    def _ok_camera(self):
        return {
            "success": True,
            "image_data": _PNG_B64,
            "image_type": "image/jpeg",
            "file_name": "snapshot.jpg",
            "file_size": 1234,
            "room_name": "кухня",
        }

    def test_success_carries_media_refs(self):
        """A successful snapshot rides the envelope as media refs."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.process_image_with_text = Mock(return_value=("На снимке кошка спит.", None))
        queue = self._make_queue(multimodal, self._ok_camera())
        with patch("app.queue.save_uploaded_file", return_value="taskid/x.jpg"):
            result = queue._process_camera_subtask(self._task())
        assert result["status"] == "completed"
        assert "На снимке кошка спит." in result["response"]
        assert result["file_path"] == "taskid/x.jpg"
        assert result["file_name"] == "snapshot.jpg"
        assert result["file_type"] == "image/jpeg"
        assert result["file_size"] == 1234
        # The question sent to the model is the framed TASK question, not the
        # raw imperative goal.
        sent = multimodal.process_image_with_text.call_args[0][1]
        assert "Найди по камерам" in sent and "task_question" not in sent

    def test_router_marker_reply_retried_with_analysis_prompt(self):
        """A [-IMAGE-EDIT-] echo is a misroute: retry once with pure analysis."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.process_image_with_text = Mock(
            side_effect=[
                ("[-IMAGE-EDIT-] Сделайте снимок тамбура и проверьте наличие кошки.", None),
                ("На снимке пусто.", None),
            ]
        )
        queue = self._make_queue(multimodal, self._ok_camera())
        with patch("app.queue.save_uploaded_file", return_value="taskid/x.jpg"):
            result = queue._process_camera_subtask(self._task())
        assert "[-IMAGE-EDIT-]" not in result["response"]
        assert "На снимке пусто." in result["response"]
        assert multimodal.process_image_with_text.call_count == 2

    def test_marker_only_after_retry_is_error(self):
        """Still a marker after the retry → error envelope, no leak."""
        marker = "[-IMAGE-] Сделайте снимок"
        multimodal = Mock()
        multimodal.available = True
        multimodal.process_image_with_text = Mock(return_value=(marker, None))
        queue = self._make_queue(multimodal, self._ok_camera())
        with patch("app.queue.save_uploaded_file", return_value=None):
            result = queue._process_camera_subtask(self._task())
        assert "[-IMAGE-]" not in str(result.get("response", ""))
        assert "⚠️ Failed to process image request" in str(result.get("response", ""))

    def test_empty_reply_retried_then_error(self):
        """Empty (thinking-only) answer → one retry, then a real error."""
        multimodal = Mock()
        multimodal.available = True
        multimodal.process_image_with_text = Mock(side_effect=[("", None), ("", None)])
        queue = self._make_queue(multimodal, self._ok_camera())
        with patch("app.queue.save_uploaded_file", return_value=None):
            result = queue._process_camera_subtask(self._task())
        assert multimodal.process_image_with_text.call_count == 2
        assert "⚠️ Failed to process image request" in str(result.get("response", ""))

    def test_camera_failure_is_error_envelope(self):
        """A snapshot API failure returns an error envelope (step fails)."""
        multimodal = Mock()
        multimodal.available = True
        queue = self._make_queue(
            multimodal,
            {"success": False, "error": "Не удалось получить изображение с камеры кухня: Таймаут (15с)"},
        )
        result = queue._process_camera_subtask(self._task())
        assert result.get("error") and "Таймаут" in result["error"]


def _cam(result):
    cam = Mock()
    cam.available = True
    cam.get_snapshot = Mock(return_value=result)
    return cam


_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
