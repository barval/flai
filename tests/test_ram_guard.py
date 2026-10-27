# tests/test_ram_guard.py
"""RAM guard for the services that are not llama models (Whisper, SLM)."""

from unittest.mock import MagicMock

import pytest

from app.resource_manager import ResourceManager

pytestmark = pytest.mark.unit


def _manager(available_ram=8192, total_ram=32768):
    rm = ResourceManager()
    rm.hardware.available_ram_mb = available_ram
    rm.hardware.total_ram_mb = total_ram
    rm.hardware.platform = "nvidia"
    return rm


def test_default_budgets_cover_the_known_services():
    rm = _manager()

    assert rm.ram_need_for("whisper") == 2000
    assert rm.ram_need_for("slm") == 2000
    assert rm.ram_need_for("postgres") == 1024


def test_a_budget_can_be_overridden_per_deployment(monkeypatch):
    rm = _manager()
    monkeypatch.setenv("WHISPER_RAM_MB", "3500")

    assert rm.ram_need_for("whisper") == 3500


def test_whisper_vram_budget_is_configurable(monkeypatch):
    rm = _manager()
    monkeypatch.setenv("WHISPER_VRAM_MB", "4200")

    assert rm.get_vram_needed_mb("whisper") == 4200


def test_enough_ram_is_accepted_without_touching_the_models():
    rm = _manager(available_ram=8192)
    rm.unload_llamacpp_model = MagicMock()

    assert rm.ensure_ram_for("whisper", 2000) is True
    rm.unload_llamacpp_model.assert_not_called()


def test_unloading_llama_models_is_the_first_lever(monkeypatch):
    """Their weights live in VRAM on a GPU host, so the RAM is page cache."""
    rm = _manager(available_ram=1500)
    rm.unload_llamacpp_model = MagicMock()
    readings = iter([1500, 6000])  # before and after the unload
    monkeypatch.setattr(ResourceManager, "_detect_available_ram_mb", lambda self: next(readings))

    assert rm.ensure_ram_for("whisper", 2000) is True
    rm.unload_llamacpp_model.assert_called_once()


def test_refusal_records_why(monkeypatch):
    rm = _manager(available_ram=1500)
    rm.unload_llamacpp_model = MagicMock()
    monkeypatch.setattr(ResourceManager, "_detect_available_ram_mb", lambda self: 1600)

    assert rm.ensure_ram_for("whisper", 2000) is False
    assert rm.last_refusal["code"] == "service_ram_short"
    assert rm.last_refusal["needed_mb"] == 2000
