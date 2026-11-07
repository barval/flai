"""`[-CAMERA-] *` means every room, not one guessed room."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from app.queue import RedisRequestQueue


def _capture(code: str, name: str) -> dict:
    return {
        "success": True,
        "room_name": name,
        "image_data": "AAAA",
        "image_type": "image/jpeg",
        "file_name": f"snap_{code}.jpg",
        "file_size": 3,
    }


def _queue(config: dict | None = None) -> Mock:
    queue = Mock()
    queue.app = Mock()
    queue.app.config = {"UPLOAD_FOLDER": "/tmp/uploads", "CAMERA_ALL_ROOMS_MAX": 12, **(config or {})}
    queue.logger = Mock()
    queue._publish_stream_event = Mock()
    queue._is_task_cancelled = Mock(return_value=False)
    queue._get_model_name = Mock(return_value="test-model")
    queue._build_error_response = Mock(return_value={"error": "built"})
    queue._save_and_respond = Mock(side_effect=[{"message_id": index} for index in range(20)])
    # Bind the methods under test to the real implementations; a bare Mock would
    # return a Mock to its own helper calls.
    queue._capture_all_rooms = lambda user_id, lang, task=None: RedisRequestQueue._capture_all_rooms(
        queue, user_id, lang, task
    )
    queue._save_camera_message = lambda *args, **kwargs: RedisRequestQueue._save_camera_message(queue, *args, **kwargs)
    queue._process_camera_all_rooms = lambda *args, **kwargs: RedisRequestQueue._process_camera_all_rooms(
        queue, *args, **kwargs
    )
    queue._camera_room_question = lambda *args, **kwargs: RedisRequestQueue._camera_room_question(queue, *args, **kwargs)
    queue._camera_summary = lambda *args, **kwargs: RedisRequestQueue._camera_summary(queue, *args, **kwargs)
    queue._is_llm_error_string = lambda text: False
    return queue


def _modules(rooms: dict[str, str], fail: set[str] | None = None) -> dict:
    fail = fail or set()
    cam = Mock()
    cam.available = True
    cam.get_available_rooms.return_value = dict(rooms)

    def snapshot(user_id, code, lang="ru", width=None):
        if code in fail:
            return {"success": False, "error": f"no signal for {code}"}
        return _capture(code, rooms[code])

    cam.get_snapshot.side_effect = snapshot
    base = Mock()
    base._ = Mock(side_effect=lambda text, lang=None, **kwargs: text)
    multimodal = Mock()
    multimodal.available = True
    # Text calls: the per-room rewrite first, then the summary over observations.
    multimodal.llamacpp = Mock()
    multimodal.llamacpp.chat.side_effect = [
        "Есть ли кошки в этой комнате?",
        "Кошки есть на балконе; в остальных комнатах их нет.",
    ]
    # One vision call per room, each answered about that room.
    multimodal.process_image_with_text.side_effect = [
        ("кошка есть", None),
        ("кошек нет", None),
        ("кошек нет", None),
        ("кошек нет", None),
        ("кошек нет", None),
    ]
    return {"cam": cam, "base": base, "multimodal": multimodal}


@pytest.mark.unit
def test_every_room_is_captured_in_a_stable_order():
    queue = _queue()
    queue.app.modules = _modules({"kuh": "Кухня", "gos": "Гостиная", "spa": "Спальня"})
    captures = RedisRequestQueue._capture_all_rooms(queue, "user", "ru", task=None)
    assert [capture["room_name"] for capture in captures] == ["Гостиная", "Кухня", "Спальня"]
    assert queue.app.modules["cam"].get_snapshot.call_count == 3


@pytest.mark.unit
def test_a_dead_camera_is_skipped_not_fatal():
    queue = _queue()
    queue.app.modules = _modules({"kuh": "Кухня", "gos": "Гостиная"}, fail={"gos"})
    captures = RedisRequestQueue._capture_all_rooms(queue, "user", "ru", task=None)
    assert [capture["room_name"] for capture in captures] == ["Кухня"]


@pytest.mark.unit
def test_the_ceiling_bounds_the_captures():
    queue = _queue({"CAMERA_ALL_ROOMS_MAX": 2})
    queue.app.modules = _modules({f"r{index}": f"Room {index}" for index in range(10)})
    captures = RedisRequestQueue._capture_all_rooms(queue, "user", "ru", task=None)
    assert len(captures) == 2
    assert queue.app.modules["cam"].get_snapshot.call_count == 2


@pytest.mark.unit
def test_progress_reports_how_many_rooms_are_left():
    queue = _queue()
    queue.app.modules = _modules({"kuh": "Кухня", "gos": "Гостиная"})
    RedisRequestQueue._capture_all_rooms(queue, "user", "ru", task={"id": "t1"})
    stages = [call.args[2] for call in queue._publish_stream_event.call_args_list]
    assert stages == [
        {"stage": "capturing_snapshot", "count": 1, "total": 2},
        {"stage": "capturing_snapshot", "count": 2, "total": 2},
    ]


@pytest.mark.unit
def test_the_asterisk_saves_one_message_per_room_and_one_answer():
    queue = _queue()
    queue.app.modules = _modules({"kuh": "Кухня", "gos": "Гостиная", "spa": "Спальня"})
    with patch("app.queue.save_uploaded_file", return_value="u/1.jpg"):
        result = RedisRequestQueue._process_camera_all_rooms(
            queue, "s1", "user", "где кошки?", "2026-10-31 15:00", "ru"
        )
    # Three snapshots plus the composed answer.
    assert len(result["messages"]) == 4
    assert queue._save_and_respond.call_count == 4
    multimodal = queue.app.modules["multimodal"]
    # One vision call per room — measured, a batch lets the model carry a subject
    # from one image into another.
    assert multimodal.process_image_with_text.call_count == 3
    prompt = multimodal.process_image_with_text.call_args.args[1]
    # The per-room question is DERIVED by the model, never hardcoded and never
    # the user's aggregate wording.
    assert prompt == "Есть ли кошки в этой комнате?"
    assert "где кошки?" not in prompt
    # The answer is the summary over the observations.
    answer = queue._save_and_respond.call_args.args[1]
    assert answer == "Кошки есть на балконе; в остальных комнатах их нет."
    summary_prompt = multimodal.llamacpp.chat.call_args.args[0][0]["content"]
    assert "Гостиная" in summary_prompt and "Кухня" in summary_prompt


@pytest.mark.unit
def test_a_failed_rewrite_and_summary_keep_the_request_and_the_observations():
    queue = _queue()
    queue.app.modules = _modules({"kuh": "Кухня"})
    queue.app.modules["multimodal"].llamacpp.chat.side_effect = RuntimeError("model down")
    with patch("app.queue.save_uploaded_file", return_value="probe/1.jpg"):
        result = RedisRequestQueue._process_camera_all_rooms(
            queue, "s1", "user", "где кошки?", "2026-10-31 16:00", "ru"
        )
    # A rewrite failure must not lose the request.
    prompt = queue.app.modules["multimodal"].process_image_with_text.call_args.args[1]
    assert "где кошки?" in prompt
    # A summary failure must not lose the observations: the list is the answer.
    saved = [call.args[1] for call in queue._save_and_respond.call_args_list]
    assert any("Кухня" in text for text in saved)
    assert len(result["messages"]) == 2


@pytest.mark.unit
def test_no_room_at_all_is_a_localized_error():
    queue = _queue()
    queue.app.modules = _modules({})
    result = RedisRequestQueue._process_camera_all_rooms(queue, "s1", "user", "где кошки?", "12:00", "ru")
    assert result == {"error": "built"}
    queue._build_error_response.assert_called_once()


@pytest.mark.unit
def test_the_router_prompt_explains_the_asterisk(app):
    from modules.base import BaseModule

    fake_cam = Mock()
    fake_cam.available = True
    fake_cam.get_all_rooms_with_forms.return_value = [("kuh", ["кухня", "Кухня"])]
    with app.app_context():
        app.modules["cam"] = fake_cam
        try:
            prompt_ru = BaseModule._build_camera_prompt_section(app.modules["base"], "ru")
            prompt_en = BaseModule._build_camera_prompt_section(app.modules["base"], "en")
        finally:
            app.modules.pop("cam", None)
    assert "[-CAMERA-] *" in prompt_ru
    assert "[-CAMERA-] *" in prompt_en


@pytest.mark.unit
def test_the_route_keeps_the_asterisk():
    from app.kernel import decision_to_route, parse_kernel_decision

    decision = parse_kernel_decision('{"intent": "camera", "query": "*"}')
    route = decision_to_route(decision, "проверь все комнаты")
    assert route["action"] == "camera"
    assert route["query"] == "*"


def test_a_user_without_camera_permissions_captures_nothing():
    """The fan-out must not even ask for rooms the account may not open.

    ``get_available_rooms`` answers with what the user may see, and the capture
    loop still calls ``get_snapshot`` per room, which enforces the permission
    again. With no permitted room the loop makes no capture at all - enumerating
    every camera and collecting a refusal each time is the leak this guards.
    """
    queue = _queue()
    modules = _modules({})
    queue.app.modules = modules
    captures = queue._capture_all_rooms("restricted", "ru")
    assert captures == []
    modules["cam"].get_snapshot.assert_not_called()


def test_a_denied_room_is_skipped_while_permitted_rooms_are_captured():
    """A permission refusal looks like any other failure: the room is dropped."""
    queue = _queue()
    modules = _modules({"kor": "Коридор", "kuh": "Кухня"}, fail={"kuh"})
    queue.app.modules = modules
    captures = RedisRequestQueue._capture_all_rooms(queue, "zoya", "ru")
    assert [item["room_name"] for item in captures] == ["Коридор"]


@pytest.mark.unit
def test_the_composed_answer_is_returned_for_the_api():
    """The result carries the answer, not only the messages it was saved as.

    Measured live at 12.9: POST /v1/chat/completions for "every room" answered
    200 with an empty choices[0].message.content while the summary sat in the
    session - the client pronounced "the local model returned no answer". The
    result envelope had no ``response`` key, which is the only field the API
    serializer reads.
    """
    queue = _queue()
    queue.app.modules = _modules({"kuh": "Кухня", "gos": "Гостиная", "spa": "Спальня"})
    with patch("app.queue.save_uploaded_file", return_value="u/1.jpg"):
        result = RedisRequestQueue._process_camera_all_rooms(
            queue, "s1", "user", "где кошки?", "2026-10-31 15:00", "ru"
        )
    answer = "Кошки есть на балконе; в остальных комнатах их нет."
    assert result["response"] == answer
    # The same text that was persisted as the last message in the session.
    assert queue._save_and_respond.call_args.args[1] == answer


@pytest.mark.unit
def test_a_dead_camera_does_not_empty_the_answer():
    """Some cameras having no signal must not turn the answer into nothing.

    Live: two of ten rooms (det, pri) refused the snapshot and were skipped; the
    answer over the remaining eight was correct and still reached the API as "".
    """
    queue = _queue()
    queue.app.modules = _modules(
        {"kuh": "Кухня", "det": "Детская", "pri": "Прихожая"}, fail={"det", "pri"}
    )
    with patch("app.queue.save_uploaded_file", return_value="u/2.jpg"):
        result = RedisRequestQueue._process_camera_all_rooms(
            queue, "s1", "user", "где кошки?", "2026-10-31 15:05", "ru"
        )
    assert result["response"] == "Кошки есть на балконе; в остальных комнатах их нет."


@pytest.mark.unit
def test_a_failed_summary_still_answers_with_the_observations():
    queue = _queue()
    modules = _modules({"kuh": "Кухня"})
    modules["multimodal"].llamacpp.chat.side_effect = RuntimeError("model down")
    queue.app.modules = modules
    with patch("app.queue.save_uploaded_file", return_value="u/3.jpg"):
        result = RedisRequestQueue._process_camera_all_rooms(
            queue, "s1", "user", "где кошки?", "2026-10-31 15:10", "ru"
        )
    assert result["response"]
    assert "Кухня" in result["response"]
    assert queue._save_and_respond.call_args.args[1] == result["response"]


@pytest.mark.unit
def test_one_room_through_the_non_streaming_path_still_returns_its_answer():
    """The single-room branch keeps working: its answer reaches the API too."""
    queue = _queue()
    queue.app.modules = _modules({"spa": "Спальня"})
    queue._process_camera_task = lambda *args, **kwargs: RedisRequestQueue._process_camera_task(queue, *args, **kwargs)
    with patch("app.queue.save_uploaded_file", return_value="u/5.jpg"):
        result = RedisRequestQueue._process_camera_task(
            queue, "spa", "s1", "user", "есть ли кошки?", "2026-10-31 15:20", "ru"
        )
    assert result["response"] == "кошка есть"
    assert len(result["messages"]) == 2
