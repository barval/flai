"""Downloader unit tests: resume, cancel, guards (network fully mocked)."""

import hashlib

import pytest

from app import model_hub

_HUB_CHUNKS = [b"x" * 1024 * 1024, b"y" * 1024 * 1024]
_HUB_SHA = hashlib.sha256(b"".join(_HUB_CHUNKS)).hexdigest()


class _Resp:
    def __init__(self, payload=None, headers=None, status_code=200, chunks=()):
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status_code
        self._chunks = list(chunks)

    def json(self):
        return self._payload

    def iter_content(self, size):
        yield from self._chunks


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.strings = {}

    def hset(self, key, mapping=None, **kw):
        self.hashes.setdefault(key, {}).update(mapping or {})
        return True

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def set(self, key, value, **kw):
        self.strings[key] = value
        return True

    def get(self, key):
        return self.strings.get(key)

    def expire(self, key, ttl):
        return True


@pytest.fixture
def hub(monkeypatch, tmp_path):
    fake_redis = FakeRedis()
    monkeypatch.setattr(model_hub, "_redis", lambda: fake_redis)
    monkeypatch.setattr(model_hub, "_post_download_scan", lambda models_dir: None)
    monkeypatch.setattr(
        model_hub,
        "_repo_files",
        lambda repo, limit=50: [
            {"path": "pooled/MyModel.Q4_K_M.gguf", "size_mb": 2.0, "sha256": _HUB_SHA},
        ],
    )

    def fake_head(url, timeout=30.0):
        return {"Content-Length": str(2 * 1024 * 1024), "ETag": '"tag-1"'}

    def fake_get(url, params=None, stream=False, extra_headers=None, timeout=30.0):
        return _Resp(
            payload={"gated": False, "private": False},
            headers={"Content-Length": str(2 * 1024 * 1024), "ETag": '"tag-1"'},
            chunks=_HUB_CHUNKS,
        )

    monkeypatch.setattr(model_hub, "_hf_head", fake_head)
    monkeypatch.setattr(model_hub, "_hf_get", fake_get)
    monkeypatch.setenv("MODELS_DIR", str(tmp_path))
    # _JOBS is a module-global registry: a job left in an active state by one
    # test (test_duplicate_download_blocked stubs the worker to a no-op) would
    # block start_download() in every later test of the same process.
    model_hub._JOBS.clear()
    yield model_hub, tmp_path
    model_hub._JOBS.clear()


def test_download_completes_with_sha(hub):
    mh, tmp_path = hub
    job_id = mh.start_download("org/My", "pooled/MyModel.Q4_K_M.gguf")
    job = None
    for _ in range(100):
        job = mh.get_job(job_id)
        if job["state"] in ("done", "failed", "cancelled"):
            break
        import time

        time.sleep(0.01)
    assert job["state"] == "done", job
    dest = tmp_path / "MyModel.Q4_K_M.gguf"
    assert dest.exists()
    assert dest.stat().st_size == 2 * 1024 * 1024
    assert ".part" not in str(dest)
    state = mh.get_job(job_id)
    assert state["received_mb"] == 2.0


def test_done_triggers_cache_rescan(hub, monkeypatch):
    mh, tmp_path = hub
    scanned = []
    monkeypatch.setattr(model_hub, "_post_download_scan", lambda models_dir: scanned.append(models_dir))
    job_id = mh.start_download("org/My", "pooled/MyModel.Q4_K_M.gguf")
    for _ in range(100):
        job = mh.get_job(job_id)
        if job["state"] in ("done", "failed", "cancelled"):
            break
        import time

        time.sleep(0.01)
    assert job["state"] == "done"
    assert scanned == [str(tmp_path)]


def test_cancel_stops_download(hub, monkeypatch):
    mh, tmp_path = hub
    # The worker must still be streaming when cancel_job() lands, otherwise the
    # 2-chunk fake stream would finish first and the job would end up "done".
    # _SlowResp parks in iter_content after the first chunk until the test has
    # issued the cancel, so the cancel provably wins the race.
    import threading

    stream_parked = threading.Event()
    cancel_issued = threading.Event()

    class _SlowResp(_Resp):
        def iter_content(self, size):
            yield _HUB_CHUNKS[0]
            stream_parked.set()
            cancel_issued.wait(5.0)
            yield _HUB_CHUNKS[1]

    def slow_get(url, params=None, stream=False, extra_headers=None, timeout=30.0):
        return _SlowResp(
            payload={"gated": False, "private": False},
            headers={"Content-Length": str(2 * 1024 * 1024), "ETag": '"tag-1"'},
        )

    monkeypatch.setattr(model_hub, "_hf_get", slow_get)
    job_id = mh.start_download("org/My", "pooled/MyModel.Q4_K_M.gguf")
    assert stream_parked.wait(5.0) is True  # first chunk written, thread parked
    assert mh.cancel_job(job_id) is True
    cancel_issued.set()
    job = None
    for _ in range(100):
        job = mh.get_job(job_id)
        if job["state"] in ("done", "failed", "cancelled"):
            break
        import time

        time.sleep(0.01)
    assert job["state"] == "cancelled"
    assert not (tmp_path / "MyModel.Q4_K_M.gguf").exists()


def test_duplicate_download_blocked(hub, monkeypatch):
    monkeypatch.setattr(model_hub, "_download_thread", lambda job: None)  # hold first job in "starting"
    mh, _ = hub
    mh.start_download("org/My", "pooled/MyModel.Q4_K_M.gguf")
    with pytest.raises(model_hub.DownloadBlocked) as exc:
        mh.start_download("org/My", "pooled/MyModel.Q4_K_M.gguf")
    assert exc.value.reason == "already_downloading"


def test_gated_repo_blocked(hub, monkeypatch):
    mh, _ = hub
    monkeypatch.setattr(model_hub, "_hf_get", lambda *a, **k: _Resp(payload={"gated": True, "private": False}))
    with pytest.raises(model_hub.DownloadBlocked) as exc:
        mh.start_download("org/My", "pooled/MyModel.Q4_K_M.gguf")
    assert exc.value.reason == "gated"


def test_oversized_blocked(hub, monkeypatch):
    monkeypatch.setenv("MODEL_HUB_MAX_FILE_GB", "0")  # 0 GB cap; fixture file is 2 MB
    mh, tmp_path = hub
    with pytest.raises(model_hub.DownloadBlocked) as exc:
        mh.start_download("org/My", "pooled/MyModel.Q4_K_M.gguf")
    assert exc.value.reason == "file_too_large"


def test_path_traversal_blocked(hub):
    mh, _ = hub
    with pytest.raises(model_hub.DownloadBlocked) as exc:
        mh.start_download("org/My", "../evil.gguf")
    assert exc.value.reason == "bad_path"


def test_resume_continues_existing_part(hub, monkeypatch):
    mh, tmp_path = hub
    monkeypatch.setattr(
        model_hub,
        "_repo_files",
        lambda repo, limit=50: [
            {"path": "pooled/MyModel.Q4_K_M.gguf", "size_mb": 2.0, "sha256": ""},
        ],
    )
    part = tmp_path / "MyModel.Q4_K_M.gguf.part"
    part.write_bytes(b"z" * (2 * 1024 * 1024))  # pretend first 2 MB done
    meta = tmp_path / "MyModel.Q4_K_M.gguf.part.meta"
    meta.write_text(f'{{"size": {2 * 1024 * 1024}, "etag": "\\"tag-1\\"", "sha": ""}}')

    seen_range = {}

    def fake_get(url, params=None, stream=False, extra_headers=None, timeout=30.0):
        seen_range["range"] = (extra_headers or {}).get("Range")
        return _Resp(
            payload={"gated": False, "private": False}, headers={"Content-Length": "0"}, chunks=[b"z" * 1024 * 1024]
        )

    monkeypatch.setattr(model_hub, "_hf_get", fake_get)
    monkeypatch.setattr(
        model_hub, "_hf_head", lambda *a, **k: {"Content-Length": str(2 * 1024 * 1024), "ETag": '"tag-1"'}
    )
    job_id = mh.start_download("org/My", "pooled/MyModel.Q4_K_M.gguf")
    for _ in range(100):
        job = mh.get_job(job_id)
        if job["state"] in ("done", "failed", "cancelled"):
            break
        import time

        time.sleep(0.01)
    assert seen_range.get("range") == "bytes=2097152-"
    assert job["state"] == "done"
    dest = tmp_path / "MyModel.Q4_K_M.gguf"
    assert dest.stat().st_size == 3 * 1024 * 1024


def test_resume_restarts_when_meta_mismatch(hub, monkeypatch):
    mh, tmp_path = hub
    part = tmp_path / "MyModel.Q4_K_M.gguf.part"
    part.write_bytes(b"junk")  # stale part with old etag
    meta = tmp_path / "MyModel.Q4_K_M.gguf.part.meta"
    meta.write_text('{"size": 123, "etag": "old"}')
    seen = {}

    def fake_get(url, params=None, stream=False, extra_headers=None, timeout=30.0):
        seen["range"] = (extra_headers or {}).get("Range")
        return _Resp(
            payload={"gated": False, "private": False},
            headers={"Content-Length": str(2 * 1024 * 1024)},
            chunks=_HUB_CHUNKS,
        )

    monkeypatch.setattr(model_hub, "_hf_get", fake_get)
    monkeypatch.setattr(
        model_hub, "_hf_head", lambda *a, **k: {"Content-Length": str(2 * 1024 * 1024), "ETag": '"tag-1"'}
    )
    job_id = mh.start_download("org/My", "pooled/MyModel.Q4_K_M.gguf")
    for _ in range(100):
        job = mh.get_job(job_id)
        if job["state"] in ("done", "failed", "cancelled"):
            break
        import time

        time.sleep(0.01)
    assert seen.get("range") is None
    assert job["state"] == "done"
