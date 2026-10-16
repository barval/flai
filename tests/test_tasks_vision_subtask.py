# tests/test_tasks_vision_subtask.py
"""Vision sub-task handler of the Tasks subsystem (v12.6).

The vision step is a pure READER: it must extract data from the attached
images in the two-section format (detailed description + all text) via the
extraction prompt, NOT answer the planner goal through the image-request
classifier. User-visible bug: a math-problem photo produced only the
transcribed text with no description, because the step passed its goal to the
classifier (`image_text.template`).

Every image is read on its own and its file name is written before the
extraction by the caller, not by the model. User-visible bug: 4 attached
images came back with ONE description — the extraction prompt mandates
EXACTLY one description block, so a joint call over several images reads only
one of them, the verifier rejects the step and the planner asks the user for
images it already has.

A step reads only the image its goal names (the camera «one step per room»
pattern: one vision step per image, the file name in the goal). User-visible
bug: a 4-image task planned one vision step per image, but every step re-read
ALL four images (7148 prompt tokens per step, four file-name blocks in each
body, 16 multimodal calls instead of 4). A goal that names no attached file
keeps the old behaviour — it reads them all.
"""

import gettext
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

_TWO_SECTIONS = (
    "=== ОПИСАНИЕ ИЗОБРАЖЕНИЯ ===\n"
    "На фото чертёж трапеции ABCD.\n\n"
    "=== ТЕКСТ НА ИЗОБРАЖЕНИИ ===\n"
    "Дано: ABCD — трапеция. Доказать: AB = CD."
)
_TWO_SECTIONS_OTHER = (
    "=== ОПИСАНИЕ ИЗОБРАЖЕНИЯ ===\n"
    "На фото жёлтое яблоко на белом фоне.\n\n"
    "=== ТЕКСТ НА ИЗОБРАЖЕНИИ ===\n"
    "ТЕКСТ ОТСУТСТВУЕТ"
)


def _labelled(name: str, extraction: str) -> str:
    """One image's block of the step body: the file name, then the extraction."""
    return f"=== IMAGE FILE NAME ===\n{name}\n\n{extraction}"


@pytest.mark.unit
class TestVisionSubtask:
    """_process_vision_subtask: extraction prompt, per-image reads, file names."""

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

    def _task(self, images, goal=""):
        return {
            "id": "63f" + "0" * 29,  # UUID-shaped id (task/vision media layout)
            "data": {
                "images": images,
                "text": goal,
                "prompt": goal or "Реши задачу на картинке",
                "task_question": goal or "Реши задачу на картинке",
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
        (two sections), never the image-request classifier — and the file name
        precedes the extraction (user request)."""
        mm = self._mm()
        mm.process_image_with_text = Mock(return_value=(_TWO_SECTIONS, None))
        queue = self._make_queue(mm)

        result = queue._process_vision_subtask(self._task([{"data": "data:image/jpeg;base64,QQ==", "name": "a.jpg"}]))

        assert result["status"] == "completed"
        assert result["response"] == _labelled("a.jpg", _TWO_SECTIONS)
        # The text handed to the multimodal model MUST be empty (extraction
        # prompt); the planner goal must never reach it.
        assert mm.process_image_with_text.call_args[0][1] == ""
        assert "Реши задачу" not in str(mm.process_image_with_text.call_args)

    def test_every_image_is_read_and_labelled_with_its_file_name(self):
        """Several images ride ONE call EACH (the extraction prompt mandates a
        single description, so a joint call reads only one image) and every
        extraction is preceded by its own file-name block (user request)."""
        mm = self._mm()
        mm.process_image_with_text = Mock(side_effect=[(_TWO_SECTIONS, None), (_TWO_SECTIONS_OTHER, None)])
        queue = self._make_queue(mm)
        images = [
            {"data": "data:image/jpeg;base64,QQ==", "name": "a.jpg"},
            {"data": "data:image/jpeg;base64,Qg==", "name": "b.jpg"},
        ]

        result = queue._process_vision_subtask(self._task(images))

        assert result["status"] == "completed"
        assert mm.process_image_with_text.call_count == 2
        assert mm.process_images_with_text.call_count == 0
        assert result["response"] == "\n\n".join(
            [_labelled("a.jpg", _TWO_SECTIONS), _labelled("b.jpg", _TWO_SECTIONS_OTHER)]
        )
        # Every read uses the extraction prompt (empty text): the planner goal
        # must never reach the multimodal model.
        for call in mm.process_image_with_text.call_args_list:
            assert call[0][1] == ""
        assert "Реши задачу" not in str(mm.process_image_with_text.call_args_list)

    def test_the_step_reads_only_the_image_its_goal_names(self):
        """One vision step per image: the goal names that image's file, so the
        step reads THAT image — not every attachment (live defect: each of the
        four steps of a 4-image task re-read all four images)."""
        mm = self._mm()
        mm.process_image_with_text = Mock(return_value=(_TWO_SECTIONS_OTHER, None))
        queue = self._make_queue(mm)
        images = [
            {"data": "d1", "name": "141ad92025c749a88cc75f1da3007d7c.png"},
            {"data": "d2", "name": "1f29e286cbc74c138d18d40ab104d898.png"},
        ]

        result = queue._process_vision_subtask(
            self._task(
                images,
                goal="Извлеки подробное описание изображения 1f29e286cbc74c138d18d40ab104d898.png и весь видимый текст с него.",
            )
        )

        assert mm.process_image_with_text.call_count == 1
        assert mm.process_image_with_text.call_args[0][0] == "d2", "the named image, not the first one"
        assert result["response"] == _labelled("1f29e286cbc74c138d18d40ab104d898.png", _TWO_SECTIONS_OTHER)

    def test_the_file_name_is_matched_without_regard_to_case(self):
        """The name reaches the goal through an LLM: its casing must not decide
        whether the step reads one image or all of them."""
        mm = self._mm()
        mm.process_image_with_text = Mock(return_value=(_TWO_SECTIONS, None))
        queue = self._make_queue(mm)
        images = [{"data": "d1", "name": "apple.png"}, {"data": "d2", "name": "pear.png"}]

        result = queue._process_vision_subtask(self._task(images, goal="Опиши, что на изображении PEAR.PNG."))

        assert mm.process_image_with_text.call_count == 1
        assert result["response"] == _labelled("pear.png", _TWO_SECTIONS)

    def test_a_goal_naming_no_image_reads_them_all(self):
        """Fallback: a goal that names no attached file covers every image (one
        step for the whole set), as before — no regression for such plans."""
        mm = self._mm()
        mm.process_image_with_text = Mock(side_effect=[(_TWO_SECTIONS, None), (_TWO_SECTIONS_OTHER, None)])
        queue = self._make_queue(mm)
        images = [{"data": "d1", "name": "a.jpg"}, {"data": "d2", "name": "b.jpg"}]

        result = queue._process_vision_subtask(self._task(images, goal="Опиши приложенные изображения."))

        assert mm.process_image_with_text.call_count == 2
        assert result["response"] == "\n\n".join(
            [_labelled("a.jpg", _TWO_SECTIONS), _labelled("b.jpg", _TWO_SECTIONS_OTHER)]
        )

    def test_image_without_a_name_still_gets_a_file_name_block(self):
        """A nameless image is still labelled, so no extraction is orphaned."""
        mm = self._mm()
        mm.process_image_with_text = Mock(return_value=(_TWO_SECTIONS, None))
        queue = self._make_queue(mm)

        result = queue._process_vision_subtask(self._task([{"data": "d1", "name": ""}]))

        assert result["response"] == _labelled("Image", _TWO_SECTIONS)

    def test_one_unreadable_image_fails_the_step_without_partial_text(self):
        """One image failing fails the whole step: a half-read body would be
        rejected by the verifier, and the planner would ask the user for the
        images it already has."""
        mm = self._mm()
        mm.process_image_with_text = Mock(side_effect=[(_TWO_SECTIONS, None), (None, "HTTP error 502")])
        queue = self._make_queue(mm)
        images = [{"data": "d1", "name": "a.jpg"}, {"data": "d2", "name": "b.jpg"}]

        result = queue._process_vision_subtask(self._task(images))

        assert result.get("error")
        assert "response" not in result

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


@pytest.mark.unit
class TestVisionFileNameMarker:
    """The file-name marker is user-visible: the compiled catalogs must carry it
    (a stale .mo silently falls back to the English source in the Russian view)."""

    def test_resolves_in_both_catalogs(self):
        root = Path(__file__).resolve().parents[1]
        for lang, expected in (("en", "IMAGE FILE NAME"), ("ru", "ИМЯ ФАЙЛА С ИЗОБРАЖЕНИЕМ")):
            mo = root / "translations" / lang / "LC_MESSAGES" / "messages.mo"
            with mo.open("rb") as fh:
                assert gettext.GNUTranslations(fh).gettext("IMAGE FILE NAME") == expected
