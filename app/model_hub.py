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
import re
import shutil
import struct
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

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

# Repos are processed in parallel during search; each one does a few sequential
# round-trips to huggingface.co (config.json tree, GGUF header Range GET).
_HUB_WORKERS = 10


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


def _module_for_type(mtype: str) -> str:
    """Module key used by fit estimation, matching the client's type->module map."""
    return {"reasoning": "reasoning", "multimodal": "multimodal", "embedding": "embedding"}.get(mtype, "reasoning")


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
_gguf_arch_cache: dict[tuple[str, str | None], dict | None] = {}
_files_cache: dict[str, list[dict]] = {}
_cache_lock = threading.Lock()
_gguf_arch_cache_lock = threading.Lock()

_arch_cache_disk_path: str | None = None


def _hub_arch_cache_path() -> str | None:
    """Disk cache file for the arch metadata, lazy-resolved once.

    Active only when the models directory exists (the mounted volume in the
    container); on bare dev hosts and in tests there is no write target, so
    persistence is skipped silently.
    """
    global _arch_cache_disk_path
    if _arch_cache_disk_path is None:
        models_dir = os.getenv("MODELS_DIR", "/models")
        if os.path.isdir(models_dir):
            _arch_cache_disk_path = os.path.join(models_dir, "hub_arch_cache.json")
    return _arch_cache_disk_path


def _load_arch_cache_from_disk() -> None:
    """Restore the arch metadata caches of the previous session."""
    path = _hub_arch_cache_path()
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning(f"model_hub arch cache load failed: {exc}")
        return
    with _cache_lock:
        for repo, value in (data.get("arch") or {}).items():
            _arch_cache[repo] = value
    with _gguf_arch_cache_lock:
        for key, value in (data.get("gguf") or {}).items():
            repo, _, path_part = key.partition("\x00")
            _gguf_arch_cache[(repo, path_part or None)] = value


def _persist_arch_cache() -> None:
    """Write both arch caches to disk after a mutation (atomic replace)."""
    path = _hub_arch_cache_path()
    if not path:
        return
    data = {"arch": {}, "gguf": {}}
    with _cache_lock:
        data["arch"] = dict(_arch_cache)
    with _gguf_arch_cache_lock:
        for (repo, path_part), value in _gguf_arch_cache.items():
            data["gguf"][f"{repo}\x00{path_part or ''}"] = value
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning(f"model_hub arch cache persist failed: {exc}")


# GGUF headers are read with a growing prefix: most answer inside 4 MB, but
# repos with a large tokenizer.merges block (e.g. HauhauCS Qwen3.8-27B) place
# the <arch>.block_count sizing keys past the 4 MB mark.
_GGUF_HEADS = (4 * 1024 * 1024, 16 * 1024 * 1024, 64 * 1024 * 1024)

_headers: dict[str, str] = {}

# Restore yesterday's arch metadata so repeated searches stay fast after a
# container restart (the per-session caches live below).
_load_arch_cache_from_disk()


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


# Multi-part GGUF shard naming, e.g. BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf.
# Only the first part is a self-contained model; the rest are companions.
_MULTIPART_RE = re.compile(r"^(?P<prefix>.*)-(?P<num>\d{4,5})-of-(?P<total>\d{4,5})\.gguf$")


def is_aux_file(path: str) -> bool:
    """True for auxiliary files that are not loadable models: imatrix files,
    MTP heads, and non-first multi-part shards."""
    base = os.path.basename(path)
    low = base.lower()
    if "imatrix" in low:
        return True
    if low.startswith("mtp-") or "/mtp/" in low:
        return True
    m = _MULTIPART_RE.match(base)
    return bool(m and int(m.group("num")) > 1)


def _family_token(name: str) -> str:
    """First alphanumeric family token of a model file name, e.g. 'qwen3.8'
    for both 'Qwen3.8-27B-UD-IQ1_S.gguf' and 'MTP/mtp-Qwen3.8-27B-Q4_0.gguf'."""
    low = re.sub(r"^mtp[-_.]", "", os.path.basename(name).lower())
    m = re.search(r"[a-z][a-z0-9]*", low)
    return m.group(0) if m else ""


def _companion_files(repo: str, model_path: str) -> list[dict]:
    """Files required to use ``model_path``: later multi-part shards and the
    matching MTP head (same model family in the repo)."""
    files = _repo_files(repo)
    out: list[dict] = []
    main_base = os.path.basename(model_path)
    m = _MULTIPART_RE.match(main_base)
    if m and int(m.group("num")) == 1:
        prefix, total = m.group("prefix"), int(m.group("total"))
        for fi in files:
            fm = _MULTIPART_RE.match(os.path.basename(fi["path"]))
            if fm and fm.group("prefix") == prefix and int(fm.group("total")) == total and int(fm.group("num")) > 1:
                out.append(dict(fi))
    token = _family_token(main_base)
    if token:
        for fi in files:
            base = os.path.basename(fi["path"])
            if base.lower().startswith("mtp-") and _family_token(base) == token:
                out.append(dict(fi))
    uniq: dict[str, dict] = {}
    for fi in out:
        uniq.setdefault(fi["path"], fi)
    return list(uniq.values())


def _search_repo(r: dict, context_length: int | None = None) -> dict | None:
    """Build one search result entry for a repo dict from the HF /api/models list."""
    repo_id = r.get("id", "")
    model_files = [f for f in _repo_files(repo_id) if not is_aux_file(f["path"])]
    if not model_files:
        return None
    model_files = [
        {
            **f,
            "companion_mb": round(sum(c["size_mb"] for c in _companion_files(repo_id, f["path"])), 1),
        }
        for f in model_files
    ]
    arch = get_repo_arch(repo_id) or {}
    mtype = classify_model_type(repo_id, arch.get("arch") or [])
    if context_length:
        # Fit is computed here (parallel across repos) so the client renders
        # ready-made ratings and never spends minutes on sequential /fit calls.
        module = _module_for_type(mtype)
        model_files = [{**f, "fit": _file_fit(repo_id, f["path"], module, context_length)} for f in model_files]
    return {
        "repo": repo_id,
        "downloads": r.get("downloads", 0),
        "likes": r.get("likes", 0),
        "gated": bool(r.get("gated")),
        "license": r.get("license") or (r.get("cardData") or {}).get("license"),
        "type": mtype,
        "arch_max_ctx": int(arch.get("arch_max_ctx") or 0),
        "files": model_files,
    }


def search_hf(query: str = "", limit: int = 20, context_length: int | None = None) -> list[dict]:
    """Top GGUF downloads matching ``query``, each with its GGUF files.

    When ``context_length`` is given, every model file carries a ready ``fit``
    estimation; the GGUF header reads run concurrently across repos.
    """
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
    repos = [r for r in resp.json() if not r.get("private")]
    if not repos:
        return []
    with ThreadPoolExecutor(max_workers=_HUB_WORKERS) as executor:
        items = list(executor.map(lambda r: _search_repo(r, context_length), repos))
    return [item for item in items if item]


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
    _persist_arch_cache()
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


_MISS = object()

# A repo whose first GGUF header reads came back without block_count gets up
# to this many further HTTP attempts (one per model file) before sibling files
# stop re-downloading headers; a real repo rarely needs more than 2 hits.
_GGUF_NEG_TRIES_LIMIT = 3
_gguf_arch_neg_attempts: dict[str, int] = {}


def _gguf_arch_cached(repo: str, file_path: str) -> dict | None | object:
    """Short lookups under the cache lock (never performs I/O) — distingishes
    an explicit negative (-) from a cache miss, so a failed first header read
    is not repeated as a blind bulk over every sibling file."""
    key = (repo, file_path)
    with _gguf_arch_cache_lock:
        if key in _gguf_arch_cache:
            value = _gguf_arch_cache[key]
            return dict(value) if value else None
        repo_wide = _gguf_arch_cache.get((repo, None))
        return dict(repo_wide) if repo_wide else _MISS


def _gguf_arch(repo: str, file_path: str) -> dict | None:
    """Read GGUF sizing metadata via HTTP Range.

    Cached per file so an auxiliary file without ``block_count`` (an imatrix,
    an MTP head, a split shard) never poisons the whole repository. A positive
    result is stored repo-wide so sibling files reuse it. The first file of an
    unknown repo gets the full probe heads; siblings are probed with a single
    small Range (a 4 MB header carries token/block_count for virtually every
    GGUF), and after ``_GGUF_NEG_TRIES_LIMIT`` consecutive misses no more
    network reads happen for the repo. The HTTP reads run outside the cache
    lock (double-checked), so concurrent fit computations across repos are
    genuinely parallel.
    """
    cached = _gguf_arch_cached(repo, file_path)
    if cached is not _MISS:
        return cached

    with _gguf_arch_cache_lock:
        if _gguf_arch_neg_attempts.get(repo, 0) >= _GGUF_NEG_TRIES_LIMIT:
            return None
        key = (repo, file_path)
        full_heads = (repo, None) not in _gguf_arch_cache
    heads = _GGUF_HEADS if full_heads else _GGUF_HEADS[:1]

    result = None
    try:
        for head in heads:
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

    with _gguf_arch_cache_lock:
        if key in _gguf_arch_cache:
            return dict(_gguf_arch_cache[key]) if _gguf_arch_cache[key] else None
        _gguf_arch_cache[key] = result
        if result:
            _gguf_arch_cache[(repo, None)] = result
            _gguf_arch_neg_attempts.pop(repo, None)
        elif (repo, None) not in _gguf_arch_cache:
            _gguf_arch_cache[(repo, None)] = None
            _gguf_arch_neg_attempts[repo] = _gguf_arch_neg_attempts.get(repo, 0) + 1
    _persist_arch_cache()
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


def _file_fit(repo: str, file_path: str, module: str, context_length: int) -> dict:
    """Per-file fit for the search result; never raises (error entries render as ✗)."""
    try:
        return estimate_fit(repo, file_path, module=module, context_length=context_length)
    except DownloadBlocked as exc:
        return {"error": exc.reason}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"fit failed for {repo}/{file_path}: {exc}")
        return {"error": "hub_failed"}


def estimate_fits(repo: str, module: str = "multimodal", context_length: int = 8192) -> dict:
    """Fit classification for every model file of ``repo`` at once.

    Backs the batched /fit-all endpoint: the client sends one request per repo
    instead of one per file. Caches (arch, GGUF header, file list) are warm
    after a search, so the per-file work here is mostly arithmetic.
    """
    out: dict[str, dict] = {}
    for fi in _repo_files(repo):
        if is_aux_file(fi["path"]):
            continue
        try:
            out[fi["path"]] = estimate_fit(repo, fi["path"], module=module, context_length=context_length)
        except DownloadBlocked as exc:
            out[fi["path"]] = {"error": exc.reason}
    return out


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
        self.parts: list[dict] = []
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
        "parts": list(job.parts),
        "part_count": len(job.parts),
        "companions_mb": round(sum(p.get("size_mb", 0) for p in job.parts[1:]), 1),
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
        # Companion parts travel along: later multi-part shards and the MTP head.
        # A companion already on disk is skipped, so free-space check and the
        # download both account only for what will actually be fetched.
        companions = []
        for c in _companion_files(repo, file_path):
            if not os.path.exists(os.path.join(models_dir, os.path.basename(c["path"]))):
                companions.append(c)
        companion_mb = sum(c["size_mb"] for c in companions)
        try:
            free_mb = shutil.disk_usage(models_dir).free // (1024 * 1024)
        except OSError:
            free_mb = 0
        if free_mb < target["size_mb"] + companion_mb + int(os.getenv("MODEL_HUB_FREE_MARGIN_GB", "4")) * 1024:
            raise DownloadBlocked("no_disk_space")

        dest = os.path.join(models_dir, model_name)
        if os.path.exists(dest):
            raise DownloadBlocked("already_present")

        job.parts = [{"path": file_path, "size_mb": target["size_mb"], "sha256": target["sha256"]}, *companions]
        job.total_mb = float(target["size_mb"]) + companion_mb
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
    try:
        parts = job.parts or [{"path": job.path, "size_mb": 0, "sha256": job.sha256}]
        total = 0
        for p in parts:
            url = f"{HF_DL}/{job.repo}/resolve/main/{p['path']}"
            head = _hf_head(url)
            total += int(head.get("Content-Length") or 0)
        job.total_mb = round(total / (1024 * 1024), 1)
        for p in parts:
            dest = os.path.join(job.models_dir, os.path.basename(p["path"]))
            try:
                _download_part(job, p)
            except DownloadCancelled:
                for suffix in (".part", ".part.meta"):
                    with contextlib.suppress(OSError):
                        os.remove(dest + suffix)
                raise
        job.state = "done"
        try:
            _post_download_scan(job.models_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"post-download GGUF cache rescan failed: {exc}")
    except DownloadCancelled:
        job.state = "cancelled"
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


def _download_part(job: _Job, part: dict) -> None:
    model_name = os.path.basename(part["path"])
    dest = os.path.join(job.models_dir, model_name)
    part_path = dest + ".part"
    meta_path = dest + ".part.meta"
    url = f"{HF_DL}/{job.repo}/resolve/main/{part['path']}"
    head = _hf_head(url)
    total = int(head.get("Content-Length") or 0)
    etag = head.get("X-Linked-Etag") or head.get("ETag") or ""

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
    resumable = os.path.exists(part_path) and prev_meta.get("size") == total and prev_meta.get("etag") == etag
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"size": total, "etag": etag, "sha": part.get("sha256", "")}, f)

    start = 0
    sha = hashlib.sha256()
    mode = "wb"
    if resumable:
        with open(part_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha.update(chunk)
        start = os.path.getsize(part_path)
        mode = "ab"

    job.state = "downloading"
    _write_progress(job)
    extra = {"Range": f"bytes={start}-"} if start else None
    resp = _hf_get(url, stream=True, extra_headers=extra, timeout=_timeout())

    last_time = time.monotonic()
    last_received = 0.0
    with open(part_path, mode) as f:
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
    if part.get("sha256"):
        got = sha.hexdigest()
        if got != part["sha256"]:
            raise DownloadFailed(f"sha256 mismatch: {got[:12]}… vs {part['sha256'][:12]}…")

    os.replace(part_path, dest)
    with contextlib.suppress(OSError):
        os.remove(meta_path)


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
