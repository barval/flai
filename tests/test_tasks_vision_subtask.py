# tests/test_tasks_vision_subtask.py
"""Vision sub-task handler of the Tasks subsystem (v12.6).

The vision step is a pure READER: it must extract data from the attached
images in the two-section format (detailed description + all text) via the
extraction prompt, NOT answer the planner goal through the image-request
classifier. User-visible bug: a math-problem photo produced only the
transcribed text with no description, because the step passed its goal to the
classifier (`image_text.template`).
"""

from unittest.mock import Mock, patch

import pytest

_TWO_SECTIONS = (
    "=== ОПИСАНИЕ ИЗОБРАЖЕНИЯ ===\n"
    "На фото чертёж трапеции ABCD.\n\n"
    "=== ТЕКСТ НА ИЗОБРАЖЕНИИ ===\n"
    "Дано: ABCD — трапеция. Доказать: AB = CD."
)


@pytest.mark.unit
class TestVisionSubtask:
    """_process_vision_subtask: extraction prompt, joint call, fallback."""

    @pytest.fixture(autouse=True)
    def _no_workers(self):
        with patch("app.queue.RedisRequestQueue.start_worker"):
            yield

    def _make_queue(self, multimodal):
        from app.queue import RedisRequestQueue

        app = Mock()
        app.config = {
            "REDIS_URL": "redis://localhost:6379/0",
            "SECRET_KEY": "test-secret-key",
            "UPLOAD_FOLDER": "/tmp/uploads-test",
        }
        app.logger = Mock()
        base = Mock()
        base._ = lambda message, lang="ru", **kw: message
        app.modules = {"base": base, "multimodal": multimodal}
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

    def _task(self, images):
        return {
            "id": "63f" + "0" * 29,  # UUID-shaped id (task/vision media layout)
            "data": {
                "images": images,
                "prompt": "Реши задачу на картинке",
                "task_question": "Реши задачу на картинке",
            },
            "session_id": "task:t1",
            "user_id": "tester",
            "lang": "ru",
        }

    def _mm(self):
        mm = Mock()
        mm.available = True
        return mm

    def test_single_image_uses_extraction_prompt_not_the_goal(self):
        """The step reads the image: empty user text selects ``image.template``
        (two sections), never the image-request classifier."""
        mm = self._mm()
        mm.process_image_with_text = Mock(return_value=(_TWO_SECTIONS, None))
        queue = self._make_queue(mm)

        result = queue._process_vision_subtask(self._task([{"data": "data:image/jpeg;base64,QQ==", "name": "a.jpg"}]))

        assert result["status"] == "completed"
        assert result["response"] == _TWO_SECTIONS
        # The text handed to the multimodal model MUST be empty (extraction
        # prompt); the planner goal must never reach it.
        assert mm.process_image_with_text.call_args[0][1] == ""
        assert "Реши задачу" not in str(mm.process_image_with_text.call_args)

    def test_multi_image_uses_one_joint_extraction_call(self):
        """Several images still ride ONE multimodal call, now with empty text."""
        mm = self._mm()
        mm.process_images_with_text = Mock(return_value=(_TWO_SECTIONS, None))
        queue = self._make_queue(mm)
        images = [
            {"data": "data:image/jpeg;base64,QQ==", "name": "a.jpg"},
            {"data": "data:image/jpeg;base64,Qg==", "name": "b.jpg"},
        ]

        result = queue._process_vision_subtask(self._task(images))

        assert result["response"] == _TWO_SECTIONS
        assert mm.process_images_with_text.call_count == 1
        assert mm.process_images_with_text.call_args[0][1] == ""

    def test_multi_image_fallback_joins_per_image_extractions(self):
        """A failed joint call degrades to per-image extraction, joined."""
        mm = self._mm()
        mm.process_images_with_text = Mock(return_value=(None, "HTTP error 502"))
        mm.describe_images_for_context = Mock(
            return_value=[
                ("d1", "=== ... ===\nописание A", None),
                ("d2", "=== ... ===\nописание B", None),
            ]
        )
        queue = self._make_queue(mm)
        images = [{"data": "d1", "name": "a.jpg"}, {"data": "d2", "name": "b.jpg"}]

        result = queue._process_vision_subtask(self._task(images))

        assert "описание A" in result["response"]
        assert "описание B" in result["response"]

    def test_no_image_is_error_envelope(self):
        """No attached image → localized error envelope (step fails)."""
        mm = self._mm()
        queue = self._make_queue(mm)

        result = queue._process_vision_subtask(self._task([]))

        assert result.get("error")
        assert "response" not in result

    def test_multimodal_unavailable_is_error_envelope(self):
        mm = self._mm()
        mm.available = False
        queue = self._make_queue(mm)

        result = queue._process_vision_subtask(self._task([{"data": "data:image/jpeg;base64,QQ==", "name": "a.jpg"}]))

        assert result.get("error")
