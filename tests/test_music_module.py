"""Music client tests: structure tags, duration guard, result parsing.

No GPU, no sidecar: every HTTP call is replaced. The module is the only thing
FLAI knows about ACE-Step, so these tests pin the contract the queue relies on.
"""

import json as jsonlib
from pathlib import Path

import pytest

from modules.music import MusicError, MusicModule, fit_duration, normalize_lyrics, plain_lyrics


class _Resp:
    def __init__(self, payload=None, status=200, content=b"RIFF"):
        self._payload = payload
        self.status_code = status
        self._content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, _size):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_normalize_lyrics_adds_structure():
    text = normalize_lyrics("Над рекой туман,\nтихий свет в окне.\nЯ иду один.")
    assert text.startswith("[verse]")
    assert "[chorus]" in text


def test_normalize_lyrics_keeps_existing_tags():
    text = "[verse]\nline one\n[chorus]\nline two"
    assert normalize_lyrics(text) == text


def test_empty_lyrics_are_refused():
    with pytest.raises(MusicError) as err:
        MusicModule().generate("pop", "   ", duration_s=30)
    assert err.value.code == "lyrics"


def test_duration_above_the_cap_is_refused_not_clamped(monkeypatch):
    monkeypatch.setenv("MUSIC_MAX_DURATION_S", "120")
    with pytest.raises(MusicError) as err:
        MusicModule().generate("pop", "one\ntwo", duration_s=121)
    assert err.value.code == "duration"


def test_is_available_is_false_when_the_sidecar_is_down(monkeypatch):
    def boom(*_a, **_k):
        raise ConnectionError("refused")

    monkeypatch.setattr("modules.music.requests.get", boom)
    assert MusicModule().is_available(force=True) is False


def test_generate_parses_the_wrapped_result_and_downloads(monkeypatch, tmp_path: Path):
    module = MusicModule()
    module._available = True
    module._checked_at = 10**12
    monkeypatch.setenv("MUSIC_URL", "http://music.test")

    inner = [{"file": "/v1/audio?path=x.wav", "status": 1, "seed_value": "42", "dit_model": "acestep-v15-turbo"}]
    state = {"polls": 0}

    def fake_post(url, json=None, timeout=None, **kwargs):  # noqa: A002 - requests API
        if url.endswith("/v1/init"):
            return _Resp({"data": {"slot": 1}})
        if url.endswith("/release_task"):
            return _Resp({"code": 200, "data": {"task_id": "t-1", "status": "queued"}})
        if url.endswith("/query_result"):
            state["polls"] += 1
            status = 1 if state["polls"] > 1 else 0
            payload = [inner[0] if status == 1 else {"file": "", "status": 0}]
            return _Resp({"data": [{"task_id": "t-1", "status": status, "result": jsonlib.dumps(payload)}]})
        raise AssertionError(url)

    monkeypatch.setattr("modules.music.requests.post", fake_post)
    monkeypatch.setattr(
        "modules.music.requests.get",
        lambda url, stream=None, timeout=None, **k: _Resp({"status": "ok"}),
    )

    result = module.generate("pop ballad", "одна строка\nвторая строка", duration_s=20, out_dir=tmp_path)
    assert Path(result["path"]).exists()
    assert result["seed"] == "42"
    assert Path(result["path"]).read_bytes() == b"RIFF"


def test_failed_task_is_an_error_not_an_empty_file(monkeypatch, tmp_path: Path):
    module = MusicModule()
    module._available = True
    module._checked_at = 10**12

    def fake_post(url, json=None, timeout=None, **kwargs):  # noqa: A002
        if url.endswith("/v1/init"):
            return _Resp({"data": {"slot": 1}})
        if url.endswith("/release_task"):
            return _Resp({"code": 200, "data": {"task_id": "t-2"}})
        return _Resp({"data": [{"task_id": "t-2", "status": 2, "result": jsonlib.dumps([{"status": 2}])}]})

    monkeypatch.setattr("modules.music.requests.post", fake_post)
    monkeypatch.setattr("modules.music.requests.get", lambda *a, **k: _Resp({}))

    with pytest.raises(MusicError) as err:
        module.generate("pop", "a\nb", duration_s=20, out_dir=tmp_path)
    assert err.value.code == "failed"
    assert list(tmp_path.iterdir()) == []


def test_music_vram_estimate_uses_the_measured_peak():
    from app.vram_estimate import music_vram_mb

    assert music_vram_mb(30) == 6900
    assert music_vram_mb(60) > music_vram_mb(30)
    assert music_vram_mb(600) <= 8000


def test_plain_lyrics_hide_the_structure_tags():
    text = "[verse]\nНад рекой туман,\nтихий свет в окне.\n[chorus]\nЯ иду один."
    shown = plain_lyrics(text)
    assert "[verse]" not in shown and "[chorus]" not in shown
    assert "Над рекой туман" in shown


def test_duration_grows_with_the_lyric():
    six = "\n".join(f"line {i}" for i in range(6))
    twelve = "\n".join(f"line {i}" for i in range(12))
    assert fit_duration(six, 30) >= 30
    assert fit_duration(twelve, 30) > fit_duration(six, 30)


def test_audio_format_defaults_to_wav_and_accepts_mp3(monkeypatch):
    module = MusicModule()
    monkeypatch.delenv("MUSIC_FORMAT", raising=False)
    assert module.audio_format == "mp3"
    monkeypatch.setenv("MUSIC_FORMAT", "WAV")
    assert module.audio_format == "wav"
    monkeypatch.setenv("MUSIC_FORMAT", "exe")
    assert module.audio_format == "mp3"
