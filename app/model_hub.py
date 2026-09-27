"""Model Hub: Hugging Face catalog search, VRAM fit estimation and downloads.

Used by the admin "Model Hub" tab. Talks to the public HF REST API through
`requests` only (no huggingface_hub dependency). All HTTP calls are bounded
by a read timeout; downloads are capped by MODEL_HUB_MAX_FILE_GB.

Task 4 appends the downloader (hashlib/json/shutil/time/uuid) — the
import block below is the client baseline; redis is already imported here
because ``_redis()`` below needs it.
"""

import contextlib
import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid

import redis
import requests

from app.vram_estimate import _classify_model_fit

logger = logging.getLogger(__name__)

HF_API = "https://huggingface.co/api/models"
HF_DL = "https://huggingface.co"

_NC_MARKERS = ("nc", "non-commercial", "noncommercial", "cc-by-nc", "personal")


class HubError(RuntimeError):
    """Low-level HF API/HTTP failure."""


class DownloadBlocked(ValueError):  # noqa: N818 - public contract name, not a naming slip
    """A download was rejected before starting; ``reason`` is a machine code
    the blueprint maps to a localized message."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class DownloadCancelled(Exception):  # noqa: N818 - public contract name, not a naming slip
    pass


class DownloadFailed(RuntimeError):  # noqa: N818 - public contract name, not a naming slip
    pass


# Module-level caches (in-memory; lost on restart, that is fine for v1)
_arch_cache: dict[str, dict | None] = {}
_files_cache: dict[str, list[dict]] = {}
_cache_lock = threading.Lock()

_headers: dict[str, str] = {}


def _hf_headers() -> dict[str, str] | None:
    if "Authorization" not in _headers:
        token = os.getenv("HUGGINGFACE_TOKEN", "").strip()
        if token:
            _headers["Authorization"] = f"Bearer {token}"
    return _headers if _headers else None


def _timeout() -> int:
    try:
        return int(os.getenv("MODEL_HUB_TIMEOUT_S", "3600"))
    except ValueError:
        return 3600


def _check(resp: requests.Response) -> None:
    if resp.status_code != 200:
        raise HubError(f"HF API {resp.status_code}: {resp.url}")


def _hf_get(
    url: str, params=None, stream: bool = False, extra_headers: dict | None = None, timeout: float = 30.0
) -> requests.Response:
    resp = requests.get(
        url, params=params, headers={**(_hf_headers() or {}), **(extra_headers or {})}, stream=stream, timeout=timeout
    )
    _check(resp)
    return resp


def _hf_head(url: str, timeout: float = 30.0):
    resp = requests.head(url, headers=_hf_headers(), allow_redirects=True, timeout=timeout)
    _check(resp)
    return resp.headers


def _redis() -> redis.Redis:
    return redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def _repo_files(repo: str, limit: int = 50) -> list[dict]:
    """GGUF files of a repo: [{path, size_mb, sha256}], cached per repo."""
    with _cache_lock:
        if repo in _files_cache:
            return list(_files_cache[repo])
    resp = _hf_get(f"{HF_API}/{repo}", params={"full": "true", "blobs": "true"}, timeout=30)
    data = resp.json()
    files = []
    for sib in data.get("siblings", []):
        path = sib.get("rfilename", "")
        if not path.endswith(".gguf") or "mmproj" in path.lower():
            continue
        size = sib.get("size") or 0
        oid = (sib.get("lfs") or {}).get("oid", "")
        files.append(
            {
                "path": path,
                "size_mb": round(size / (1024 * 1024), 1),
                "sha256": (oid or "").lower(),
            }
        )
    files = files[:limit]
    with _cache_lock:
        _files_cache[repo] = files
    return list(files)


def search_hf(query: str = "", limit: int = 20) -> list[dict]:
    """Top GGUF downloads matching ``query``, each with its GGUF files."""
    resp = _hf_get(
        HF_API,
        params={
            "search": query,
            "filter": "gguf",
            "sort": "downloads",
            "direction": "-1",
            "limit": min(int(limit), 50),
        },
        timeout=30,
    )
    items = []
    for r in resp.json():
        if r.get("private"):
            continue
        files = _repo_files(r.get("id", ""))
        if not files:
            continue
        items.append(
            {
                "repo": r.get("id", ""),
                "downloads": r.get("downloads", 0),
                "likes": r.get("likes", 0),
                "gated": bool(r.get("gated")),
                "license": r.get("license") or (r.get("cardData") or {}).get("license"),
                "files": files,
            }
        )
    return items


def get_repo_arch(repo: str) -> dict | None:
    """Read block_count / expert_count / architectures from the repo's config.json."""
    with _cache_lock:
        if repo in _arch_cache:
            cached = _arch_cache[repo]
            return dict(cached) if cached else None
    result: dict | None = None
    try:
        resp = _hf_get(f"{HF_API}/{repo}/tree/main", params={"path": "config.json"}, timeout=30)
        entries = resp.json()
        cfg = None
        for entry in entries:
            path = entry.get("path", "")
            if path.endswith("config.json"):
                cfg = _hf_get(f"{HF_DL}/{repo}/resolve/main/{path}", timeout=30).json()
                break
        if cfg:
            result = {
                "block_count": int(cfg.get("num_hidden_layers") or 0) or None,
                "expert_count": int(cfg.get("num_local_experts") or 0),
                "arch": cfg.get("architectures") or [],
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"get_repo_arch({repo}) failed: {exc}")
        result = None
    with _cache_lock:
        _arch_cache[repo] = result
    return dict(result) if result else None


def estimate_fit(repo: str, file_path: str, module: str = "multimodal", context_length: int = 8192) -> dict:
    """Classify fit of ``file_path`` in ``repo`` for ``module`` without the file present."""
    target = None
    for fi in _repo_files(repo):
        if fi["path"] == file_path:
            target = fi
            break
    if target is None:
        raise DownloadBlocked("not_found")
    arch = get_repo_arch(repo)
    if not arch or not arch.get("block_count"):
        raise DownloadBlocked("unknown_arch")
    model_name = os.path.basename(file_path)
    fit = _classify_model_fit(
        model_name,
        context_length,
        file_size_mb=float(target["size_mb"]),
        block_count=int(arch["block_count"]),
        module=module,
    )
    fit["expert_count"] = int(arch["expert_count"] or 0)
    fit["block_count"] = int(arch["block_count"])
    fit["arch"] = ",".join(arch["arch"] or [])
    fit["sha256"] = target["sha256"]
    fit["context_length"] = int(context_length)
    return fit


def license_hint(license_name) -> str:
    if not license_name:
        return "ok"
    low = str(license_name).lower()
    return "nc" if any(m in low for m in _NC_MARKERS) else "ok"


# ---- downloader ----------------------------------------------------------

_NC_DONE = 86400  # job TTL (seconds)


class _Job:
    def __init__(self, job_id: str, repo: str, file_path: str, model_name: str, models_dir: str):
        self.job_id = job_id
        self.repo = repo
        self.path = file_path
        self.filename = model_name
        self.models_dir = models_dir
        self.total_mb = 0.0
        self.received_mb = 0.0
        self.speed_mb_s = 0.0
        self.sha256 = ""
        self.state = "starting"
        self.error = ""
        self.shutdown = threading.Event()
        self.thread: threading.Thread | None = None


_JOBS: dict[str, _Job] = {}
_jobs_lock = threading.Lock()


def _job_to_dict(job: _Job) -> dict:
    return {
        "job_id": job.job_id,
        "repo": job.repo,
        "path": job.path,
        "filename": job.filename,
        "total_mb": job.total_mb,
        "received_mb": round(job.received_mb, 1),
        "speed_mb_s": round(job.speed_mb_s, 1),
        "state": job.state,
        "error": job.error,
        "models_dir": job.models_dir,
    }


def _post_download_scan(models_dir: str) -> None:
    """Re-scan GGUF metadata cache after a successful download so the Models
    tab sees the new file without a manual 'Refresh models'."""

    from app.utils import sync_gguf_models_cache

    sync_gguf_models_cache(models_dir)


def _write_progress(job: _Job) -> None:
    try:
        r = _redis()
        r.hset(f"model_hub:job:{job.job_id}", mapping=_job_to_dict(job))
        r.expire(f"model_hub:job:{job.job_id}", _NC_DONE)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"model_hub progress write failed: {exc}")


def _is_cancelled(job: _Job) -> bool:
    if job.shutdown.is_set():
        return True
    with contextlib.suppress(Exception):
        if _redis().get(f"model_hub:cancel:{job.job_id}"):
            job.shutdown.set()
            return True
    return False


def start_download(repo: str, file_path: str, models_dir: str | None = None) -> str:
    """Validate + enqueue a download; returns a job id. Raises DownloadBlocked."""
    if ".." in file_path.split("/") or file_path.startswith("/"):
        raise DownloadBlocked("bad_path")
    with _jobs_lock:
        active = (j.state in ("starting", "downloading", "verifying") for j in _JOBS.values())
        if any(active):
            raise DownloadBlocked("already_downloading")

    target = None
    for fi in _repo_files(repo):
        if fi["path"] == file_path:
            target = fi
            break
    if target is None:
        raise DownloadBlocked("not_found")

    model_name = os.path.basename(file_path)
    max_file_mb = int(os.getenv("MODEL_HUB_MAX_FILE_GB", "40")) * 1024
    if target["size_mb"] > max_file_mb:
        raise DownloadBlocked("file_too_large")

    # gating: the downloader itself does not know gated status (files exist even
    # for gated repos on the API). The /download route checks the search item.
    # Here we re-check using the repo detail endpoint.
    try:
        info = _hf_get(f"{HF_API}/{repo}", timeout=30).json()
        if info.get("gated") or info.get("private"):
            raise DownloadBlocked("gated")
    except HubError as exc:
        raise DownloadBlocked("not_found") from exc

    if models_dir is None:
        models_dir = os.getenv("MODELS_DIR", "/models")
    os.makedirs(models_dir, exist_ok=True)
    try:
        free_mb = shutil.disk_usage(models_dir).free // (1024 * 1024)
    except OSError:
        free_mb = 0
    if free_mb < target["size_mb"] + int(os.getenv("MODEL_HUB_FREE_MARGIN_GB", "4")) * 1024:
        raise DownloadBlocked("no_disk_space")

    dest = os.path.join(models_dir, model_name)
    if os.path.exists(dest):
        raise DownloadBlocked("already_present")

    job = _Job(uuid.uuid4().hex[:12], repo, file_path, model_name, models_dir)
    job.total_mb = float(target["size_mb"])
    job.sha256 = target["sha256"]
    with _jobs_lock:
        _JOBS[job.job_id] = job
    _write_progress(job)
    job.thread = threading.Thread(target=_download_thread, args=(job,), daemon=True)
    job.thread.start()
    return job.job_id


def _download_thread(job: _Job) -> None:
    dest = os.path.join(job.models_dir, job.filename)
    part = dest + ".part"
    meta_path = dest + ".part.meta"
    try:
        url = f"{HF_DL}/{job.repo}/resolve/main/{job.path}"
        head = _hf_head(url)
        total = int(head.get("Content-Length") or 0)
        etag = head.get("X-Linked-Etag") or head.get("ETag") or ""
        job.total_mb = round(total / (1024 * 1024), 1)

        start = 0
        sha = hashlib.sha256()
        mode = "wb"
        if os.path.exists(part) and os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except Exception:  # noqa: BLE001
                meta = {}
            if meta.get("size") == total and meta.get("etag") == etag:
                with open(part, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        sha.update(chunk)
                start = os.path.getsize(part)
                mode = "ab"

        job.state = "downloading"
        _write_progress(job)
        extra = {"Range": f"bytes={start}-"} if start else None
        resp = _hf_get(url, stream=True, extra_headers=extra, timeout=_timeout())

        last_time = time.monotonic()
        last_received = 0.0
        with open(part, mode) as f:
            for chunk in resp.iter_content(1 << 20):
                if not chunk:
                    continue
                if _is_cancelled(job):
                    raise DownloadCancelled(job.job_id)
                f.write(chunk)
                sha.update(chunk)
                job.received_mb += len(chunk) / (1024 * 1024)
                now = time.monotonic()
                if now - last_time >= 1.0:
                    job.speed_mb_s = (job.received_mb - last_received) / (now - last_time)
                    last_received, last_time = job.received_mb, now
                    _write_progress(job)

        job.state = "verifying"
        _write_progress(job)
        if job.sha256:
            got = sha.hexdigest()
            if got != job.sha256:
                raise DownloadFailed(f"sha256 mismatch: {got[:12]}… vs {job.sha256[:12]}…")

        os.replace(part, dest)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"size": total, "etag": etag, "sha": job.sha256}, f)
        job.state = "done"
        try:
            _post_download_scan(job.models_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"post-download GGUF cache rescan failed: {exc}")
    except DownloadCancelled:
        job.state = "cancelled"
        for p in (part, meta_path):
            with contextlib.suppress(OSError):
                os.remove(p)
    except DownloadFailed as exc:
        job.state = "failed"
        job.error = str(exc)
        # .part kept for future resume
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"model_hub download failed: {exc}")
        job.state = "failed"
        job.error = str(exc)[:500]
    finally:
        _write_progress(job)


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _JOBS.get(job_id)
    if job is None:
        return None
    return _job_to_dict(job)


def cancel_job(job_id: str) -> bool:
    with _jobs_lock:
        job = _JOBS.get(job_id)
    if job is None or job.state not in ("starting", "downloading", "verifying"):
        return False
    job.shutdown.set()
    with contextlib.suppress(Exception):
        _redis().set(f"model_hub:cancel:{job.job_id}", "1", ex=_NC_DONE)
    return True
