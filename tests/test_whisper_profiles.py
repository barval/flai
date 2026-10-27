# tests/test_whisper_profiles.py
"""Whisper CPU/GPU selection: compose profiles and the derived endpoint."""

from pathlib import Path

import pytest
import yaml

from modules.audio import whisper_service_url

ROOT = Path(__file__).resolve().parent.parent


def _services(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))["services"]


@pytest.mark.unit
def test_the_endpoint_follows_the_profile_flag():
    assert whisper_service_url(False) == "http://flai-whisper:9000/asr"
    assert whisper_service_url(True) == "http://flai-whisper-gpu:9000/asr"


@pytest.mark.unit
def test_gpu_compose_offers_both_whisper_profiles():
    services = _services("docker-compose.gpu.yml")

    assert "with-whisper-cpu" in services["whisper"]["profiles"]
    assert services["whisper-gpu"]["profiles"] == ["with-whisper-gpu"]
    assert services["whisper-gpu"]["container_name"] == "flai-whisper-gpu"
    assert services["whisper-gpu"]["environment"].count("ASR_DEVICE=cuda") == 1


@pytest.mark.unit
def test_cpu_compose_has_no_gpu_whisper():
    services = _services("docker-compose.cpu.yml")

    assert "with-whisper-cpu" in services["whisper"]["profiles"]
    assert "whisper-gpu" not in services


@pytest.mark.unit
def test_tts_profiles_do_not_start_the_asr_container():
    """A GPU-ASR deployment selects TTS alone: `--profile with-tts-piper`."""
    gpu = _services("docker-compose.gpu.yml")

    assert "with-tts-piper" in gpu["piper"]["profiles"]
    assert "with-tts-kokoro" in gpu["kokoro"]["profiles"]
    assert "with-whisper-gpu" not in gpu["piper"]["profiles"]
