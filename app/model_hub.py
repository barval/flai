"""Model Hub: Hugging Face catalog search, VRAM fit estimation and downloads.

Used by the admin "Model Hub" tab. Talks to the public HF REST API through
`requests` only (no huggingface_hub dependency). All HTTP calls are bounded
by a read timeout; downloads are capped by MODEL_HUB_MAX_FILE_GB.

Task 4 appends the downloader (hashlib/json/shutil/time/uuid) — the
import block below is the client baseline; redis is already imported here
because ``_redis()`` below needs it.
"""

import logging
import os
import threading

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
