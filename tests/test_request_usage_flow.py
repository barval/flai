"""End-to-end token usage flow: the fast-worker dispatcher begins a per-request
usage account, the requeue carries it into the reasoning task, and
_save_and_respond consumes the merged totals into the stored message/reply.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.utils import (
    begin_usage_account,
    current_usage_account_id,
    record_usage_for_current,
    reset_usage_accounts,
)


@pytest.fixture
def q(test_app):
    from app.queue import RedisRequestQueue

    with (
        patch("redis.from_url", return_value=MagicMock()),
        patch.object(RedisRequestQueue, "__init__", lambda self, app: None),
    ):
        q = RedisRequestQueue(test_app)
        q.app = test_app
        q.logger = test_app.logger
        return q


@pytest.fixture(autouse=True)
def _cleanup_usage():
    yield
    reset_usage_accounts()


def test_save_and_respond_consumes_active_account_and_reports_full_duration(q):
    """With a live account the save uses real token totals and the response_time
    covers the whole request (from submission), not just generation."""
    with patch("app.queue.save_message", return_value=42) as store:
        begin_usage_account("req-full", submitted_at=1000.0)
        record_usage_for_current(500, 120)
        result = q._save_and_respond(
            "s1", "Answer", "model", 0.5, is_error=False, response_style="neutral", user_id="u1"
        )

    _, kwargs = store.call_args
    assert kwargs["prompt_tokens"] == 500
    assert kwargs["completion_tokens"] == 120
    # response_time overridden to full request duration (NOT the passed 0.5)
    assert result["response_time"] > 0.5
    assert result["prompt_tokens"] == 500
    assert result["completion_tokens"] == 120
    # Account consumed — nothing leaks into the next request
    assert current_usage_account_id() is None


def test_save_and_respond_falls_back_to_estimate_without_account(q):
    from app.utils import estimate_tokens

    with patch("app.queue.save_message", return_value=42) as store:
        result = q._save_and_respond(
            "s1", "Hi there", "model", 0.4, is_error=False, response_style="neutral", user_id="u1"
        )

    _, kwargs = store.call_args
    assert kwargs["prompt_tokens"] is None
    assert kwargs["completion_tokens"] == estimate_tokens("Hi there")  # estimate fallback
    assert result["completion_tokens"] == estimate_tokens("Hi there")
    assert result["prompt_tokens"] is None


def test_requeue_reasoning_task_carries_request_usage(q):
    """The re-queued reasoning task data embeds request id, submission time and
    router-accumulated usage so the slow worker merges everything into one."""
    with patch.object(q, "add_request", return_value=("new-task-id", {"position": 1, "estimated_seconds": 0})) as add:
        begin_usage_account("orig-req", submitted_at=1700.0)
        record_usage_for_current(150, 10)
        q._requeue_reasoning_task("вопрос", "s1", "u1", "ru", "neutral")

    request_data = add.call_args.args[2]
    assert request_data["request_id"] == "orig-req"
    assert request_data["submitted_at"] == 1700.0
    assert request_data["usage_accum"] == {"prompt_tokens": 150, "completion_tokens": 10}
