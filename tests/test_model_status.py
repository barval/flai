"""The "model is loaded" answer that decides whether a load is announced.

A status that says "loading the model" while the model is already resident is a
lie the user notices, and it is the same lie on GPU and on CPU: both modes are
served by llama-swap, so ``/running`` is the single source of truth.
"""

from __future__ import annotations

import pytest

from app import model_status


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _patch(monkeypatch, payload=None, status_code=200, config=None, entry="reasoning"):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code, payload))
    monkeypatch.setattr("app.model_config.get_model_config", lambda module, _raw=False: config or {})
    monkeypatch.setattr("app.universal_mode.entry_name", lambda module: entry)


def test_ready_entry_means_no_load(monkeypatch):
    _patch(
        monkeypatch,
        payload={"running": [{"model": "reasoning", "state": "ready", "cmd": "llama-server -m /models/x.gguf"}]},
        config={"model_name": "x.gguf"},
    )

    assert model_status.model_is_ready("reasoning") is True


def test_a_starting_model_is_not_ready(monkeypatch):
    _patch(
        monkeypatch,
        payload={"running": [{"model": "reasoning", "state": "starting", "cmd": "llama-server -m /models/x.gguf"}]},
        config={"model_name": "x.gguf"},
    )

    assert model_status.model_is_ready("reasoning") is False


def test_the_universal_entry_serves_both_roles(monkeypatch):
    _patch(
        monkeypatch,
        payload={"running": [{"model": "universal", "state": "ready", "cmd": "llama-server -m /models/x.gguf"}]},
        config={"model_name": "x.gguf"},
        entry="universal",
    )

    assert model_status.model_is_ready("multimodal") is True
    assert model_status.model_is_ready("reasoning") is True


def test_an_unreachable_backend_is_not_ready(monkeypatch):
    import requests

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(requests, "get", boom)

    assert model_status.model_is_ready("reasoning") is False


@pytest.mark.parametrize(("enabled", "expected"), [(True, "loading_model"), (False, "loading_reasoning_model")])
def test_the_stage_wording_follows_the_mode(monkeypatch, enabled, expected):
    from app.tasks import executor

    monkeypatch.setattr("app.model_status.universal_mode_active", lambda: enabled)

    assert executor._load_stage() == expected
