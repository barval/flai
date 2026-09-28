"""Model Hub: Hugging Face catalog search, VRAM fit estimation and downloads.

Used by the admin "Model Hub" tab. Talks to the public HF REST API through
`requests` only (no huggingface_hub dependency). All HTTP calls are bounded
by a read timeout; downloads are capped by MODEL_HUB_MAX_FILE_GB.

Two layers live here: the catalog client (search, repo file listing, arch
read, fit estimation) and the downloader (one job at a time, resumable
``.part`` files, progress published to Redis, cancel flag, sha256 verify).
"""

import contextlib
import hashlib
import json
import logging
import os
import shutil
import struct
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

MODEL_TYPES = ("reasoning", "multimodal", "embedding")

_MULTIMODAL_ARCH = ("vision", "mllama", "composite", "owl", "florence", "pali")
_EMBEDDING_ARCH = ("bert", "bge", "nomic", "gte", "embedding", "withlintransformerpooler", "lora")
_MULTIMODAL_NAME = ("vl", "vision", "multimodal", "mplug", "ollama")
_EMBEDDING_NAME = ("embed", "bge", "mxbai", "gte", "nomic", "e5-", "instructor", "arctic-embed")


def classify_model_type(repo: str, archs: list[str] | None = None) -> str:
    """Classify a model as reasoning / multimodal / embedding.

    Prioritizes the repo's architecture names (config.json), then falls back to
    the repo/file name heuristics. Unknown cases default to ``reasoning``.
    """
    for arch in archs or []:
        low = arch.lower()
        if any(m in low for m in _MULTIMODAL_ARCH):
            return "multimodal"
    for arch in archs or []:
        low = arch.lower()
        if any(m in low for m in _EMBEDDING_ARCH):
            return "embedding"
    low_repo = repo.lower()
    if any(m in low_repo for m in _MULTIMODAL_NAME):
        return "multimodal"
    if any(m in low_repo for m in _EMBEDDING_NAME):
        return "embedding"
    return "reasoning"


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
_gguf_arch_cache: dict[str, dict | None] = {}
_files_cache: dict[str, list[dict]] = {}
_cache_lock = threading.Lock()
_gguf_arch_cache_lock = threading.Lock()
# GGUF headers are read with a growing prefix: most answer inside 4 MB, but
# repos with a large tokenizer.merges block (e.g. HauhauCS Qwen3.8-27B) place
# the <arch>.block_count sizing keys past the 4 MB mark.
_GGUF_HEADS = (4 * 1024 * 1024, 16 * 1024 * 1024, 64 * 1024 * 1024)

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
    # 206 is the answer of a ranged GET, which the resumable downloader sends
    # when it continues a partial .part file.
    if resp.status_code not in (200, 206):
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


_rcli: redis.Redis | None = None


def _redis() -> redis.Redis:
    # Memoized: the downloader polls progress/cancel once per 1 MB chunk, and
    # redis.from_url() builds a new connection pool (plus a TCP connect) on
    # every call.
    global _rcli
    if _rcli is None:
        _rcli = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    return _rcli


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
        arch = get_repo_arch(r.get("id", "")) or {}
        items.append(
            {
                "repo": r.get("id", ""),
                "downloads": r.get("downloads", 0),
                "likes": r.get("likes", 0),
                "gated": bool(r.get("gated")),
                "license": r.get("license") or (r.get("cardData") or {}).get("license"),
                "type": classify_model_type(r.get("id", ""), arch.get("arch") or []),
                "arch_max_ctx": int(arch.get("arch_max_ctx") or 0),
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
            max_ctx = int(cfg.get("max_position_embeddings") or 0) or 0
            rope = (cfg.get("rope_scaling") or {}).get("factor") or 1.0
            try:
                result["arch_max_ctx"] = int(max_ctx * float(rope))
            except (TypeError, ValueError):
                result["arch_max_ctx"] = int(max_ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"get_repo_arch({repo}) failed: {exc}")
        result = None
    with _cache_lock:
        _arch_cache[repo] = result
    return dict(result) if result else None


def _parse_gguf_arch(data: bytes) -> dict | None:
    """Extract architecture sizing metadata from a GGUF header prefix."""
    if len(data) < 24 or data[:4] != b"GGUF":
        return None

    kv_count = struct.unpack_from("<Q", data, 16)[0]
    offset = 24
    architecture = None
    result: dict = {}

    def read_string(position: int) -> tuple[str, int]:
        length = struct.unpack_from("<Q", data, position)[0]
        position += 8
        end = position + length
        if end > len(data):
            raise struct.error("GGUF string exceeds range data")
        return data[position:end].decode("utf-8", "replace"), end

    def skip_value(position: int, value_type: int) -> int:
        fixed_sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
        if value_type in fixed_sizes:
            return position + fixed_sizes[value_type]
        if value_type == 8:
            length = struct.unpack_from("<Q", data, position)[0]
            return position + 8 + length
        if value_type == 9:
            element_type = struct.unpack_from("<I", data, position)[0]
            count = struct.unpack_from("<Q", data, position + 4)[0]
            position += 12
            if element_type == 8:
                for _ in range(count):
                    length = struct.unpack_from("<Q", data, position)[0]
                    position += 8 + length
                return position
            if element_type not in fixed_sizes:
                raise struct.error("Unsupported GGUF array element type")
            return position + fixed_sizes[element_type] * count
        raise struct.error("Unsupported GGUF metadata type")

    for _ in range(kv_count):
        try:
            key, offset = read_string(offset)
            value_type = struct.unpack_from("<I", data, offset)[0]
            offset += 4

            if key == "general.architecture" and value_type == 8:
                architecture, offset = read_string(offset)
                result["arch"] = [architecture]
                continue

            wanted = {
                f"{architecture}.block_count": "block_count",
                f"{architecture}.context_length": "arch_max_ctx",
                f"{architecture}.expert_count": "expert_count",
            }
            if architecture and key in wanted and value_type in (4, 10):
                if value_type == 4:
                    value = struct.unpack_from("<I", data, offset)[0]
                    offset += 4
                else:
                    value = struct.unpack_from("<Q", data, offset)[0]
                    offset += 8
                result[wanted[key]] = int(value)
            elif architecture and key.startswith("tokenizer.") and result.get("block_count"):
                break
            else:
                offset = skip_value(offset, value_type)

            if result.get("block_count") and result.get("arch_max_ctx") and result.get("expert_count"):
                break
        except (struct.error, UnicodeDecodeError, OverflowError):
            break

    if not result.get("block_count"):
        return None
    result.setdefault("expert_count", 0)
    result.setdefault("arch_max_ctx", 0)
    return result


def _gguf_arch(repo: str, file_path: str) -> dict | None:
    """Read GGUF sizing metadata via HTTP Range, cached once per repository."""
    with _gguf_arch_cache_lock:
        if repo in _gguf_arch_cache:
            cached = _gguf_arch_cache[repo]
            return dict(cached) if cached else None

        result = None
        try:
            for head in _GGUF_HEADS:
                resp = _hf_get(
                    f"{HF_DL}/{repo}/resolve/main/{file_path}",
                    stream=True,
                    extra_headers={"Range": f"bytes=0-{head - 1}"},
                    timeout=30,
                )
                if resp.status_code == 206:
                    prefix = bytearray()
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        prefix.extend(chunk)
                        if len(prefix) >= head:
                            break
                    result = _parse_gguf_arch(bytes(prefix[:head]))
                if result:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"GGUF metadata read failed for {repo}/{file_path}: {exc}")

        _gguf_arch_cache[repo] = result
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
        arch = _gguf_arch(repo, target["path"])
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
    fit["arch_max_ctx"] = int(arch.get("arch_max_ctx") or fit.get("arch_max_ctx") or 0)
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
    model_name = os.path.basename(file_path)
    if models_dir is None:
        models_dir = os.getenv("MODELS_DIR", "/models")

    # The active-job check and the registration happen under one lock hold, and
    # the registration precedes every network/disk call below. Checking first
    # and registering last would leave a window (repo listing + disk_usage,
    # ~100-500 ms) in which two near-simultaneous calls both pass the check and
    # both workers open the same <name>.part in "wb" — truncating and
    # interleaving each other's bytes.
    job = _Job(uuid.uuid4().hex[:12], repo, file_path, model_name, models_dir)
    with _jobs_lock:
        if any(j.state in ("starting", "downloading", "verifying") for j in _JOBS.values()):
            raise DownloadBlocked("already_downloading")
        _JOBS[job.job_id] = job

    try:
        target = None
        for fi in _repo_files(repo):
            if fi["path"] == file_path:
                target = fi
                break
        if target is None:
            raise DownloadBlocked("not_found")

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

        job.total_mb = float(target["size_mb"])
        job.sha256 = target["sha256"]
    except BaseException:
        # A start that never reaches the worker must release its reservation,
        # otherwise a rejected (or errored) attempt would leave a zombie
        # "starting" job blocking every later download.
        with _jobs_lock:
            _JOBS.pop(job.job_id, None)
        raise

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

        # The sidecar records which upstream file the partial .part is a prefix
        # of. It is written as soon as HEAD resolves — not after the download
        # finishes — so an interrupted download leaves a resumable pair behind,
        # and it is removed again once the finished file replaces the .part.
        prev_meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    prev_meta = json.load(f)
            except Exception:  # noqa: BLE001 - unreadable sidecar: restart the .part
                prev_meta = {}
        resumable = os.path.exists(part) and prev_meta.get("size") == total and prev_meta.get("etag") == etag
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"size": total, "etag": etag, "sha": job.sha256}, f)

        start = 0
        sha = hashlib.sha256()
        mode = "wb"
        if resumable:
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
        with contextlib.suppress(OSError):
            os.remove(meta_path)
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
