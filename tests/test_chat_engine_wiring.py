"""The chat turn runs on the shared engine loop, not on its own copy of it."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from app.queue import RedisRequestQueue


@pytest.fixture(autouse=True)
def _no_workers():
    with patch("app.queue.RedisRequestQueue.start_worker"):
        yield


@pytest.fixture
def queue():
    app = Mock()
    app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
    app.logger = Mock()

    base = Mock()
    base.llamacpp = Mock()
    base._get_context_for_model.return_value = ""
    base._ = lambda text, lang="ru": text
    app.modules = {"base": base}

    redis = Mock()
    redis.blpop.return_value = None
    pipe = Mock()
    pipe.execute.return_value = []
    redis.pipeline.return_value = pipe

    with patch("app.queue.redis.from_url", return_value=redis):
        instance = RedisRequestQueue(app)
    instance._publish_stream_event = Mock()
    instance._publish_stream_token = Mock()
    instance._save_and_respond = Mock(return_value={"response": "saved"})
    instance._build_error_response = Mock(return_value={"error": "built"})
    instance._is_task_cancelled = Mock(return_value=False)
    instance._get_model_name = Mock(return_value="test-model")
    instance.add_memory_extract_task = Mock()
    return instance


TASK = {"id": "t1", "user_id": "u1", "session_id": "s1"}


def _run(queue, **kwargs):
    with (
        patch("app.queue.format_prompt", return_value="system"),
        patch("app.queue.get_tool_definitions", return_value=[{"function": {"name": "calculator", "parameters": {}}}]),
        patch("modules.base.get_style_instruction", return_value=""),
        patch("modules.base.response_language_name", return_value="Russian"),
    ):
        return queue._process_chat_with_tools(
            TASK,
            "what is 2+2",
            "2026-10-31 12:00:00",
            "s1",
            "u1",
            "en",
            "neutral",
            stream=False,
            **kwargs,
        )


@pytest.mark.unit
def test_a_tool_round_is_executed_and_the_answer_is_saved(queue):
    queue.app.modules["base"].llamacpp.chat.side_effect = [
        {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "calculator", "arguments": "{}"}}]},
        "the answer is 4",
    ]

    with patch("app.queue.execute_tool", return_value="4") as executed:
        _run(queue)

    assert executed.call_count == 1
    queue._save_and_respond.assert_called_once()
    assert queue._save_and_respond.call_args[0][1] == "the answer is 4"


@pytest.mark.unit
def test_a_tool_call_on_the_last_round_is_not_executed(queue):
    queue.app.modules["base"].llamacpp.chat.return_value = {
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "calculator", "arguments": "{}"}}],
    }

    with patch("app.queue.MAX_TOOL_ITERATIONS", 1), patch("app.queue.execute_tool") as executed:
        _run(queue)

    assert executed.call_count == 0
    queue._build_error_response.assert_called_once()


@pytest.mark.unit
def test_a_tool_call_printed_as_text_is_still_executed(queue):
    queue.app.modules["base"].llamacpp.chat.side_effect = [
        '{"name": "calculator", "arguments": {"expression": "2+2"}}',
        "the answer is 4",
    ]

    with patch("app.queue.execute_tool", return_value="4") as executed:
        _run(queue)

    assert executed.call_count == 1
    assert queue._save_and_respond.call_args[0][1] == "the answer is 4"
