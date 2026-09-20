"""Tests for the per-request LLM token usage accumulator.

The accumulator sums prompt/completion token usage across all LLM calls that
belong to one user request (router, chat generation, reasoning attempts, RLM
sub-calls). Worker threads begin/finish an account per phase; a re-queued
reasoning task re-seeds the account with the router-accumulated totals via
``bias``.
"""

from app.utils import (
    begin_usage_account,
    current_usage_account_id,
    finish_usage_account,
    record_usage_for_current,
    reset_usage_accounts,
)


def _cleanup():
    reset_usage_accounts()


def test_begin_record_finish_accumulates():
    _cleanup()
    try:
        begin_usage_account("req-1")
        record_usage_for_current(100, 20)
        record_usage_for_current(50, 3)
        totals = finish_usage_account("req-1")
        assert totals["prompt_tokens"] == 150
        assert totals["completion_tokens"] == 23
        # Popped once — second finish is a no-op returning None
        assert finish_usage_account("req-1") is None
    finally:
        _cleanup()


def test_finish_with_no_active_account_returns_none():
    _cleanup()
    try:
        assert finish_usage_account("req-none") is None
    finally:
        _cleanup()


def test_accounts_are_isolated_by_id():
    _cleanup()
    try:
        begin_usage_account("req-a")
        record_usage_for_current(10, 1)
        # Switching to another account must not leak tokens between them
        begin_usage_account("req-b")
        record_usage_for_current(500, 60)
        b_totals = finish_usage_account("req-b")
        assert b_totals["prompt_tokens"] == 500
        assert b_totals["completion_tokens"] == 60
        a_totals = finish_usage_account("req-a")
        assert a_totals["prompt_tokens"] == 10
        assert a_totals["completion_tokens"] == 1
    finally:
        _cleanup()


def test_record_without_account_is_ignored():
    _cleanup()
    try:
        record_usage_for_current(1000, 100)
        assert finish_usage_account("req-ignored") is None
    finally:
        _cleanup()


def test_bias_seeds_totals_from_previous_phase():
    _cleanup()
    try:
        # Fast worker accumulates router usage, then passes the totals into the
        # re-queued reasoning task as bias. The slow worker merges them.
        begin_usage_account("orig-1")
        record_usage_for_current(150, 10)
        router_totals = finish_usage_account("orig-1")

        begin_usage_account("orig-1", bias=router_totals)
        record_usage_for_current(300, 45)
        totals = finish_usage_account("orig-1")
        assert totals["prompt_tokens"] == 450
        assert totals["completion_tokens"] == 55
    finally:
        _cleanup()


def test_negative_or_zero_tokens_are_ignored():
    _cleanup()
    try:
        begin_usage_account("req-zero")
        record_usage_for_current(0, 0)
        record_usage_for_current(None, None)
        record_usage_for_current(-5, 3)
        totals = finish_usage_account("req-zero")
        assert totals["prompt_tokens"] == 0
        assert totals["completion_tokens"] == 3
    finally:
        _cleanup()


def test_record_prompt_tokens_feeds_active_account():
    """The llamacpp client's single choke point records real usage per response."""
    from app.llamacpp_client import _record_prompt_tokens

    _cleanup()
    try:
        begin_usage_account("req-rl")
        # Non-stream response: usage nested under the top-level 'usage' key.
        _record_prompt_tokens(
            {"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 120, "completion_tokens": 30}},
            "multimodal",
            "ru",
            [{"role": "user", "content": "Hi"}],
        )
        # Streaming usage chunk: the raw usage dict is passed directly.
        _record_prompt_tokens(
            {"prompt_tokens": 80, "completion_tokens": 12, "total_tokens": 92},
            "multimodal",
            "ru",
            [{"role": "user", "content": "Hi"}],
        )
        totals = finish_usage_account("req-rl")
        assert totals["prompt_tokens"] == 200
        assert totals["completion_tokens"] == 42
    finally:
        _cleanup()


def test_finish_without_argument_consumes_current_account():
    """The answer-saving path pops whatever account is bound to this thread."""
    _cleanup()
    try:
        begin_usage_account("req-current", submitted_at=1000.0)
        record_usage_for_current(40, 7)
        totals = finish_usage_account()
        assert totals["prompt_tokens"] == 40
        assert totals["completion_tokens"] == 7
        assert totals["submitted_at"] == 1000.0
        # Account is unbound afterwards
        assert current_usage_account_id() is None
        assert finish_usage_account() is None
    finally:
        _cleanup()


def test_current_usage_account_id_tracks_binding():
    _cleanup()
    try:
        assert current_usage_account_id() is None
        begin_usage_account("req-id")
        assert current_usage_account_id() == "req-id"
        finish_usage_account("req-id")
        assert current_usage_account_id() is None
    finally:
        _cleanup()


def test_submitted_at_is_carried_through_bias():
    _cleanup()
    try:
        # Fast worker: begin with the original submission time, accumulate
        # router usage, then pass everything to the re-queued reasoning task.
        begin_usage_account("orig-ts", submitted_at=1700.0)
        record_usage_for_current(60, 5)
        carried = finish_usage_account("orig-ts")

        begin_usage_account("orig-ts", bias=carried, submitted_at=carried["submitted_at"])
        record_usage_for_current(30, 10)
        totals = finish_usage_account("orig-ts")
        assert totals["submitted_at"] == 1700.0
        assert totals["prompt_tokens"] == 90
        assert totals["completion_tokens"] == 15
    finally:
        _cleanup()
