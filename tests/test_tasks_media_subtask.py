# tests/test_tasks_media_subtask.py
"""Tasks (Задачи) media sub-task parity.

Regression cover for the four defects reported on a running task:
  * a media tool's queue ``task_type`` must exist in ``_process_request``'s
    dispatch — the previous map sent image_gen as "image" and image_edit as
    "image_edit", neither of which is dispatched ("Unknown request type");
  * a sub-task returns its artifact in the result envelope instead of saving a
    chat message into a session that does not exist;
  * the slow/crawl worker drops a tagged sub-task whose step is gone — stop or
    delete of the running task left an orphan that kept holding the slow worker
    and the GPU, starving the next task's steps.

Executor-side payloads (``text``, task-id session, image_edit source bytes) live
in test_tasks_executor.py; gate deferral timing in test_tasks_queue_guard.py.
"""

import inspect
from unittest.mock import MagicMock, patch

import pytest

from app.queue import RedisRequestQueue
from app.tasks.executor import _SUBTASK_TYPE, _strip_data_url


def _make_queue():
    """RedisRequestQueue with a fake Redis and no workers (test_queue.py pattern)."""
    app = MagicMock()
    app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
    with patch("app.queue.redis.from_url", return_value=MagicMock()):
        return RedisRequestQueue(app, start_workers=False)


def _subtask_task(step_id="step-1", task_id="task-1"):
    """A minimal queue request tagged as a Tasks-subsystem step."""
    return {
        "id": task_id,
        "user_id": "tester",
        "session_id": task_id,
        "data": {"type": "image_gen", "text": "draw a cat", "task_step_subtask": step_id},
        "user_class": 3,
        "lang": "ru",
    }


# ── dispatch parity ─────────────────────────────────────────────────────


def test_media_tools_map_to_the_media_handlers():
    """The queue types of the media tools point at real handlers."""
    assert _SUBTASK_TYPE["image_gen"] == "image_gen"
    assert _SUBTASK_TYPE["image_edit"] == "api_image_edit"
    assert _SUBTASK_TYPE["video_gen"] == "video"


@pytest.mark.parametrize("task_type", sorted(set(_SUBTASK_TYPE.values())))
def test_every_subtask_type_is_dispatched(task_type):
    """_process_request has a branch for every queue type the executor sends."""
    source = inspect.getsource(RedisRequestQueue._process_request)
    assert f'task_type == "{task_type}"' in source or f'req_type == "{task_type}"' in source


# ── data: URL helper ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("data:image/jpeg;base64,AAAB", "AAAB"),
        ("AAAB", "AAAB"),
        ("", ""),
        ("data:image/png;base64,", ""),
    ],
)
def test_strip_data_url(value, expected):
    """The edit pipeline takes raw base64; a plain payload is untouched."""
    assert _strip_data_url(value) == expected


# ── result envelope ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestSubtaskEnvelope:
    """A tagged sub-task answers with an envelope; a chat request still saves."""

    def test_envelope_carries_the_media_refs(self):
        queue = _make_queue()
        envelope = queue._subtask_envelope(
            _subtask_task(),
            "task-1",
            "the image is ready",
            "task-1/pic.png",
            {"file_name": "pic.png", "file_type": "image/png", "file_size": 123},
        )
        assert envelope == {
            "status": "completed",
            "session_id": "task-1",
            "response": "the image is ready",
            "file_path": "task-1/pic.png",
            "file_name": "pic.png",
            "file_type": "image/png",
            "file_size": 123,
        }

    def test_envelope_without_a_file_has_no_media_keys(self):
        queue = _make_queue()
        envelope = queue._subtask_envelope(_subtask_task(), "task-1", "no media", None, {"file_name": "ignored"})
        assert envelope == {"status": "completed", "session_id": "task-1", "response": "no media"}

    def test_chat_request_keeps_the_saving_path(self):
        queue = _make_queue()
        chat = {"id": "req-1", "session_id": "sess-1", "data": {"type": "image_gen", "text": "draw a cat"}}
        assert queue._subtask_envelope(chat, "sess-1", "answer", "sess-1/pic.png", {}) is None
        assert queue._subtask_envelope(None, "sess-1", "answer", None, {}) is None

    def test_is_task_subtask(self):
        queue = _make_queue()
        assert queue._is_task_subtask(_subtask_task()) is True
        assert queue._is_task_subtask({"data": {"type": "text"}}) is False
        assert queue._is_task_subtask(None) is False


# ── model header metadata ───────────────────────────────────────────────


@pytest.mark.unit
class TestSubtaskModelMeta:
    """Every task step answers like a chat message: the envelope carries the
    model name/type (and the real token totals for LLM-backed steps), which
    the executor merges into the step's extra for the chat-style header."""

    def test_meta_helpers_build_name_type_usage(self):
        queue = _make_queue()
        queue._get_model_name = lambda module: {"multimodal": "Qwen3VL-4B"}.get(module)
        with patch("app.utils.peek_usage_account", return_value={"prompt_tokens": 120, "completion_tokens": 45}):
            meta = queue._subtask_model_meta("Qwen3VL-4B", "multimodal", with_usage=True)
        assert meta == {
            "model_name": "Qwen3VL-4B",
            "model_type": "multimodal",
            "usage": {"prompt_tokens": 120, "completion_tokens": 45},
        }

    def test_meta_without_tokens_has_no_usage_key(self):
        queue = _make_queue()
        with patch("app.utils.peek_usage_account", return_value=None):
            meta = queue._subtask_model_meta("flux-2-klein-4b", "image_gen")
        assert meta == {"model_name": "flux-2-klein-4b", "model_type": "image_gen"}

    def test_meta_zero_usage_is_dropped(self):
        queue = _make_queue()
        with patch("app.utils.peek_usage_account", return_value={"prompt_tokens": 0, "completion_tokens": 0}):
            meta = queue._subtask_model_meta("m", "multimodal", with_usage=True)
        assert meta == {"model_name": "m", "model_type": "multimodal"}

    def test_every_meta_call_requests_the_token_totals(self):
        """A sub-task that bills a model must carry its real token totals.

        ``with_usage`` defaults to False, so a call site that forgets the flag
        silently drops the tokens the multimodal/reasoning phase just recorded
        (image_gen, image_edit, video and rlm were all missing it while camera
        and vision had it). This guard reads the class source so a new media
        sub-task cannot repeat the omission.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(RedisRequestQueue)))
        missing = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "_subtask_model_meta"
            and not any(
                kw.arg == "with_usage" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
            )
        ]
        assert missing == [], (
            f"_subtask_model_meta calls without with_usage=True (offsets into the class source): {missing}"
        )

    def test_envelope_merges_the_meta(self):
        queue = _make_queue()
        envelope = queue._subtask_envelope(
            _subtask_task(),
            "task-1",
            "the image is ready",
            "task-1/pic.png",
            {"file_name": "pic.png"},
            meta={"model_name": "z_image_turbo", "model_type": "image_gen"},
        )
        assert envelope["model_name"] == "z_image_turbo"
        assert envelope["model_type"] == "image_gen"

    def test_chat_envelope_ignores_the_meta(self):
        queue = _make_queue()
        chat = {"id": "req-1", "session_id": "sess-1", "data": {"type": "image_gen", "text": "draw a cat"}}
        assert (
            queue._subtask_envelope(
                chat, "sess-1", "answer", None, {}, meta={"model_name": "m", "model_type": "image_gen"}
            )
            is None
        )


# ── orphan sub-task guard ───────────────────────────────────────────────


@pytest.mark.unit
class TestOrphanSubtaskDrop:
    """_drop_orphan_subtask: a sub-task whose step is gone must not run."""

    def test_drops_when_the_step_is_missing(self):
        queue = _make_queue()
        with patch("app.tasks.store.get_step", return_value=None):
            assert queue._drop_orphan_subtask(_subtask_task()) is True

    @pytest.mark.parametrize("status", ["failed", "passed", "interrupted", "awaiting_user"])
    def test_drops_when_the_step_is_no_longer_queued(self, status):
        queue = _make_queue()
        with patch("app.tasks.store.get_step", return_value={"id": "step-1", "status": status}):
            assert queue._drop_orphan_subtask(_subtask_task()) is True

    @pytest.mark.parametrize("status", ["subtask_queued", "subtask_running"])
    def test_keeps_a_live_step(self, status):
        queue = _make_queue()
        with patch("app.tasks.store.get_step", return_value={"id": "step-1", "status": status}):
            assert queue._drop_orphan_subtask(_subtask_task()) is False

    def test_chat_request_never_consults_the_store(self):
        queue = _make_queue()
        chat = {"id": "req-1", "session_id": "sess-1", "data": {"type": "text", "text": "hi"}}
        with patch("app.tasks.store.get_step", side_effect=AssertionError("must not be consulted")):
            assert queue._drop_orphan_subtask(chat) is False

    def test_fail_open_when_the_store_errors(self):
        queue = _make_queue()
        with patch("app.tasks.store.get_step", side_effect=RuntimeError("db down")):
            assert queue._drop_orphan_subtask(_subtask_task()) is False


# ── image generation sub-task: envelope, no chat row ────────────────────

_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


@pytest.mark.unit
class TestImageGenSubtaskResult:
    """_process_image_gen_task: a task step answers with the envelope.

    Chat parity means the generated file is saved under the queue session (the
    TASK id) and returned as media refs; nothing is written to a chat session —
    a sub-task's session does not exist, so the message would be an orphan row.
    """

    def _queue(self, image_module):
        app = MagicMock()
        app.config = {
            "REDIS_URL": "redis://localhost:6379/0",
            "SECRET_KEY": "test-secret-key",
            "UPLOAD_FOLDER": "/tmp/uploads-test",
            "SD_MODEL_TYPE": "z_image_turbo",
        }
        app.logger = MagicMock()
        base = MagicMock()
        base._ = lambda message, lang="en", **kw: message
        mm = MagicMock()
        mm.generate_image_params.return_value = ({"prompt": "a cat"}, None)
        app.modules = {"base": base, "image": image_module, "multimodal": mm}
        redis = MagicMock()
        redis.blpop.return_value = None
        redis.llen.return_value = 0
        redis.scard.return_value = 0
        redis.pipeline.return_value = MagicMock()
        with patch("app.queue.redis.from_url", return_value=redis):
            queue = RedisRequestQueue(app, start_workers=False)
        for name in (
            "_log_gpu_state_before_op",
            "_unload_llamacpp_models",
            "_unload_video_pipeline",
            "_preload_multimodal_sync",
            "_publish_stream_event",
            "_collect_reference_examples",
        ):
            setattr(queue, name, MagicMock(return_value=[]))
        queue._wait_for_vram = lambda *a: True
        queue._check_vram_ready = lambda *a: True
        queue._get_vram_needed = lambda *a: 8000
        queue._get_model_name = lambda *a: "mm"
        queue._is_task_cancelled = lambda *a: False
        queue._save_and_respond = MagicMock(side_effect=AssertionError("a task step must not save a chat message"))
        return queue

    def _task(self):
        return {
            "id": "7b" + "0" * 34,  # UUID-shaped: save_uploaded_file validates it
            "session_id": "7b" + "0" * 34,
            "user_id": "tester",
            "lang": "en",
            "data": {"type": "image_gen", "text": "draw a cat", "task_step_subtask": "step-1"},
        }

    def test_result_envelope_carries_the_media(self):
        image = MagicMock()
        image.available = True
        image._call_wrapper.return_value = {
            "success": True,
            "image_data": _PNG_B64,
            "file_name": "cat.png",
            "file_type": "image/png",
            "file_size": 4242,
        }
        queue = self._queue(image)
        task = self._task()
        saved = {}

        def _fake_save(file_data, filename, session_id, upload_folder, user_id):
            saved.update(session_id=session_id, filename=filename)
            return f"{session_id}/cat.png"

        with patch("app.queue.save_uploaded_file", side_effect=_fake_save):
            result = queue._process_image_gen_task("draw a cat", task["session_id"], "tester", "en", task=task)

        assert result == {
            "status": "completed",
            "session_id": task["id"],
            "response": "draw a cat",
            "file_path": f"{task['id']}/cat.png",
            "file_name": "cat.png",
            "file_type": "image/png",
            "file_size": 4242,
            "model_name": "z_image_turbo",
            "model_type": "image_gen",
        }
        assert saved["session_id"] == task["id"], "the file lands in the task's own folder"
        queue._save_and_respond.assert_not_called()

    def test_result_envelope_carries_the_token_usage(self):
        """The multimodal parameter call bills real tokens — they must reach the
        step header instead of dying in the worker's cleanup clause.

        A media sub-task never calls ``_save_and_respond`` (there is no chat
        message to consume the per-request account), so without the envelope the
        totals were lost and the task step showed no 🔢 line at all.
        """
        image = MagicMock()
        image.available = True
        image._call_wrapper.return_value = {
            "success": True,
            "image_data": _PNG_B64,
            "file_name": "cat.png",
            "file_type": "image/png",
            "file_size": 4242,
        }
        queue = self._queue(image)
        task = self._task()

        with (
            patch("app.queue.save_uploaded_file", return_value=f"{task['id']}/cat.png"),
            patch(
                "app.utils.peek_usage_account",
                return_value={"prompt_tokens": 1200, "completion_tokens": 180},
            ),
        ):
            result = queue._process_image_gen_task("draw a cat", task["session_id"], "tester", "en", task=task)

        assert result["model_type"] == "image_gen"
        assert result["usage"] == {"prompt_tokens": 1200, "completion_tokens": 180}

    def test_chat_request_still_saves_the_message(self):
        image = MagicMock()
        image.available = True
        image._call_wrapper.return_value = {
            "success": True,
            "image_data": _PNG_B64,
            "file_name": "cat.png",
            "file_type": "image/png",
            "file_size": 4242,
        }
        queue = self._queue(image)
        queue._save_and_respond = MagicMock(return_value={"status": "completed", "response": "saved"})
        chat_task = {"id": "req-1", "session_id": "sess-1", "user_id": "tester", "lang": "en", "data": {}}

        with patch("app.queue.save_uploaded_file", return_value="sess-1/cat.png"):
            result = queue._process_image_gen_task("draw a cat", "sess-1", "tester", "en", task=chat_task)

        assert result == {"status": "completed", "response": "saved"}
        queue._save_and_respond.assert_called_once()
