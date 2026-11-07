"""The chat model is warmed before the first message, through the VRAM gate.

The llama-swap preload hook did this and could not do it safely: it started the
model outside `ensure_vram_for()`, next to whatever was resident, and a model
that did not fit kept its weights in RAM (11 tok/s instead of 75). The warm-up
asks the gate first, so it can only produce a placement a user message could.
"""

from unittest.mock import MagicMock, patch

import pytest

from app import warmup

pytestmark = pytest.mark.unit


def _response(status=200, payload=None):
    response = MagicMock(status_code=status)
    response.json.return_value = payload or {}
    return response


class TestReadiness:
    def test_it_waits_until_llama_swap_answers(self):
        with patch("app.warmup.requests.get", side_effect=[_response(), _response()]):
            assert warmup.wait_for_llama_swap(timeout=5) is True

    def test_it_gives_up_after_the_deadline(self):
        with patch("app.warmup.requests.get", side_effect=OSError("no route")), patch("app.warmup.time.sleep"):
            assert warmup.wait_for_llama_swap(timeout=0.2) is False


class TestLoadChatModel:
    def test_a_running_model_reports_success(self):
        running = {"running": [{"model": "multimodal", "cmd": "llama-server -m /models/Qwen3VL-8B.gguf"}]}
        with (
            patch("app.warmup.requests.post", return_value=_response()),
            patch("app.warmup.requests.get", return_value=_response(payload=running)),
            patch("app.model_config.get_model_config", return_value={"model_name": "Qwen3VL-8B.gguf"}),
        ):
            assert warmup.load_chat_model(timeout=5) is True

    def test_an_http_error_is_a_failure_not_a_success(self):
        with patch("app.warmup.requests.post", return_value=_response(status=500)):
            assert warmup.load_chat_model(timeout=5) is False

    def test_a_model_that_never_reaches_running_times_out(self):
        with (
            patch("app.warmup.requests.post", return_value=_response()),
            patch("app.warmup.requests.get", return_value=_response(payload={"running": []})),
            patch("app.model_config.get_model_config", return_value={"model_name": "Qwen3VL-8B.gguf"}),
            patch("app.warmup.time.sleep"),
            patch("app.warmup.time.time", side_effect=[0, 0, 1, 2, 3, 4, 5, 6]),
        ):
            assert warmup.load_chat_model(timeout=5) is False


class TestWarmUpThroughTheGate:
    def test_the_gate_runs_before_any_load(self):
        manager = MagicMock()
        manager.ensure_vram_for.return_value = True
        with (
            patch("app.resource_manager.get_resource_manager", return_value=manager),
            patch("app.warmup.load_chat_model", return_value=True) as load,
        ):
            assert warmup.warm_up_chat_model(MagicMock()) is True
        manager.ensure_vram_for.assert_called_once_with("multimodal")
        load.assert_called_once()

    def test_a_refused_gate_does_not_load_anything(self):
        manager = MagicMock()
        manager.ensure_vram_for.return_value = False
        with (
            patch("app.resource_manager.get_resource_manager", return_value=manager),
            patch("app.warmup.load_chat_model") as load,
        ):
            assert warmup.warm_up_chat_model(MagicMock()) is False
        load.assert_not_called()


class TestScheduling:
    def test_the_warmup_runs_in_the_background(self):
        app = MagicMock()
        done = []

        def fake_warm(app_arg):
            done.append(app_arg)
            return True

        with (
            patch("app.warmup.wait_for_llama_swap", return_value=True),
            patch("app.warmup.warm_up_chat_model", side_effect=fake_warm),
        ):
            thread = warmup.schedule_chat_model_warmup(app)
            assert thread is not None
            thread.join(timeout=5)
        assert done == [app]

    def test_no_warmup_when_llama_swap_never_answers(self):
        app = MagicMock()
        with (
            patch("app.warmup.wait_for_llama_swap", return_value=False),
            patch("app.warmup.warm_up_chat_model") as warm,
        ):
            thread = warmup.schedule_chat_model_warmup(app)
            thread.join(timeout=5)
        warm.assert_not_called()


def test_the_warm_up_does_not_run_under_pytest():
    """conftest sets TESTING after create_app(); the guard must not depend on it.

    A thread per created app would wait 90 s for a llama-swap that tests never
    run, and the suite builds hundreds of apps.
    """
    from app import _is_test_process

    assert _is_test_process() is True


def test_the_post_reasoning_reload_goes_through_the_gate():
    """After a reasoning answer the chat model is loaded through the gate too.

    Live, 2026-11-05: this path asked llama-swap for the model directly while the
    reasoning model still held 12.9 GB, so only 2 587 MB of the model's 5 700 MB
    of files reached the card, the rest stayed in RAM, and every answer ran at
    7 tok/s for hours — nothing logged it.
    """
    from app.queue import RedisRequestQueue

    queue = MagicMock()
    with patch("app.warmup.warm_up_chat_model", return_value=True) as warm:
        RedisRequestQueue._preload_multimodal_sync(queue, timeout=30)
    warm.assert_called_once()
    assert warm.call_args.args[0] is queue.app


def test_the_background_preload_after_a_task_goes_through_the_gate():
    """The third load path, and the one that produced the 02:03 incident.

    `_cleanup_vram_after_task()` runs after every task. Its preload posted a
    completion request straight to llama-swap ~5 s after a reasoning answer,
    while the reasoning model still held 12.9 GB — the chat model loaded into
    the remainder with its weights in RAM, and the next answer ran at 6.7 tok/s.
    """
    import threading

    from app.queue import RedisRequestQueue

    queue = MagicMock()
    queue.is_gpu_task_active.return_value = False
    queue._chat_model_is_resident.return_value = False
    done = threading.Event()

    def fake_warm(app_arg, timeout=0):
        done.set()
        return True

    with patch("app.warmup.warm_up_chat_model", side_effect=fake_warm) as warm:
        RedisRequestQueue._preload_chat_model_background(queue)
        assert done.wait(timeout=5)

    warm.assert_called_once()
    assert warm.call_args.args[0] is queue.app


def test_the_background_preload_does_not_wake_a_resident_model():
    """The chat model has ttl 0: a warm-up after every task is a request storm.

    Measured 2026-11-05: every task sent a one-token completion, llama-swap
    answered 429 while a model was loading, and a real request ended with
    "Model crashed during startup" (llama-swap: starting reasoning failed:
    upstream command exited prematurely).
    """
    import threading

    from app.queue import RedisRequestQueue

    queue = MagicMock()
    queue.is_gpu_task_active.return_value = False
    done = threading.Event()
    queue._chat_model_is_resident.side_effect = lambda: done.set() or True

    with patch("app.warmup.warm_up_chat_model") as warm:
        RedisRequestQueue._preload_chat_model_background(queue)
        assert done.wait(timeout=5)

    warm.assert_not_called()
