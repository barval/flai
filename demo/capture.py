"""Capture the demo snapshot from a running instance (release preparation only)."""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from demo.io import DEMO_DIR, save_snapshot

CSRF_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)"')
ACCOUNT_PREFIX = "demo_"

FICTIONAL_USERS = {
    "ru": [("anna", "Анна"), ("igor", "Игорь"), ("maria", "Мария")],
    "en": [("alex", "Alex"), ("priya", "Priya"), ("tom", "Tom")],
}

# Camera room names belong to the host that ran the capture, so the English demo
# would otherwise show the Russian rooms of that machine. An unknown code falls
# back to the code itself, which keeps the page free of Cyrillic either way.
CAMERA_ROOMS_EN = {
    "bal": ["balcony", "balconies"],
    "det": ["nursery", "nurseries"],
    "gos": ["living room", "living rooms"],
    "kab": ["study", "home office"],
    "kor": ["hallway", "hallways"],
    "kuh": ["kitchen", "kitchens"],
    "pri": ["entrance hall", "front hall"],
    "spa": ["bedroom", "bedrooms"],
    "tam": ["vestibule", "vestibules"],
    "tst": ["test"],
}


class CaptureError(RuntimeError):
    """Raised when the capture cannot continue."""


class CaptureTimeoutError(CaptureError):
    """Raised when a queued task does not finish in time."""


def wait_for(predicate: Callable[[], bool], timeout_s: float, interval_s: float = 2.0) -> bool:
    """Poll ``predicate`` until it is true or the timeout expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except CaptureError:
            raise
        except Exception:  # noqa: BLE001 - a worker restart must not abort a long capture
            pass
        time.sleep(interval_s)
    raise CaptureTimeoutError(f"Condition not met within {timeout_s} s")


class DemoSession:
    """Minimal HTTP client for the running instance: login, CSRF, JSON and multipart."""

    def __init__(self, base_url: str, transport: Any = None):
        if transport is None:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            transport = requests.Session()
            retry = Retry(total=6, connect=6, read=0, status=0, backoff_factor=2.0, allowed_methods=None)
            transport.mount("http://", HTTPAdapter(max_retries=retry))
            transport.mount("https://", HTTPAdapter(max_retries=retry))
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.csrf_token = ""

    def _request(self, method: str, path: str, **kwargs):
        self._relax_secure_cookies()
        headers = dict(kwargs.pop("headers", None) or {})
        if method.upper() != "GET" and self.csrf_token:
            headers.setdefault("X-CSRFToken", self.csrf_token)
        if headers:
            kwargs["headers"] = headers
        response = self.transport.request(method, f"{self.base_url}{path}", **kwargs)
        if response.status_code >= 400:
            raise CaptureError(f"{method} {path} failed with {response.status_code}: {response.text[:200]}")
        return response

    def _relax_secure_cookies(self) -> None:
        """Allow the session cookie over plain HTTP: the product marks it Secure for HTTPS.

        The capture talks to a local instance that has no TLS terminator in front of it,
        so a Secure cookie would never be sent back and every request would bounce to the
        login page.
        """
        jar = getattr(self.transport, "cookies", None)
        if jar is None:
            return
        host = urlsplit(self.base_url).hostname or "localhost"
        for cookie in jar:
            if getattr(cookie, "secure", False):
                cookie.secure = False
            if cookie.domain and host not in cookie.domain:
                cookie.domain = host
                cookie.domain_specified = False

    def login(self, login: str, password: str) -> None:
        self._request(
            "POST",
            "/login",
            data={"login": login, "password": password, "theme": "light"},
            allow_redirects=False,
        )
        self._relax_secure_cookies()
        response = self._request("GET", "/chat", allow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            response = self._request("GET", "/admin/")
        html = response.text
        match = CSRF_RE.search(html)
        if not match:
            raise CaptureError("CSRF token not found on the chat page")
        self.csrf_token = match.group(1)

    def get_json(self, path: str) -> Any:
        return self._request("GET", path).json()

    def post_json(self, path: str, payload: dict) -> Any:
        return self._request("POST", path, json=payload, headers={"X-CSRFToken": self.csrf_token}).json()

    def post_form(self, path: str, data: dict | None = None, files: dict | None = None) -> Any:
        return self._request(
            "POST",
            path,
            data=data or {},
            files=files or {},
            headers={"X-CSRFToken": self.csrf_token},
        ).json()

    def get_bytes(self, path: str) -> bytes:
        return self._request("GET", path).content

    def post_raw(self, path: str, payload: dict) -> tuple[bytes, str]:
        response = self._request("POST", path, json=payload, headers={"X-CSRFToken": self.csrf_token})
        return response.content, response.headers.get("Content-Type", "")


SERIES_PER_HOUR = {
    "outgoing_tokens": 30000,
    "incoming_tokens": 6500,
    "messages": 18,
    "sessions": 6,
    "tasks": 1.5,
    "files": 2,
    "documents": 1.5,
    "web_requests": 20,
}
STORAGE_BYTES_BASE = 128_000_000


def _series_value(key: str, index: int, bucket_seconds: int, range_key: str) -> int:
    seed = (sum(ord(char) for char in range_key) + len(key)) % 7
    if key == "storage_bytes":
        return STORAGE_BYTES_BASE + index * 900_000 + seed * 40_000
    per_bucket = SERIES_PER_HOUR.get(key, 5) * bucket_seconds / 3600.0
    wobble = ((index * 5 + seed) % 11) / 10.0
    return int(per_bucket * (0.3 + wobble))


HOST_SERIES = {
    # (base, spread) in percent; the wobble is deterministic per bucket.
    "cpu_pct": (8, 24),
    "ram_pct": (36, 11),
    "gpu_pct": (2, 22),
    "vram_pct": (12, 38),
}


def _host_profile() -> dict:
    """Totals of the machine that captured the snapshot (RAM, then VRAM)."""
    ram_total = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    ram_total = int(line.split()[1]) // 1024
                    break
    except OSError:
        ram_total = 0
    vram_total = 0
    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first = (out.stdout or "").strip().splitlines()
        vram_total = int(first[0]) if first and first[0].strip().isdigit() else 0
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        vram_total = 0
    return {"ram_total_mb": ram_total or 16384, "vram_total_mb": vram_total or 8192}


def _host_series_value(key: str, index: int, range_key: str) -> float:
    base, spread = HOST_SERIES[key]
    seed = (sum(ord(char) for char in range_key) + len(key)) % 5
    return round(base + ((index * 3 + seed * 2) % spread), 1)


def _synthetic_monitoring(body: dict, range_key: str) -> dict:
    """Give every monitoring range plausible, distinct numbers."""
    count = len(body.get("buckets") or []) or 24
    bucket_seconds = int(body.get("bucket_seconds") or 3600)
    series = body.get("series") or {}
    for key in HOST_SERIES:
        # A snapshot captured before the host cards existed has no such series.
        series.setdefault(key, [None] * count)
    for key in list(series):
        if key in HOST_SERIES:
            series[key] = [_host_series_value(key, index, range_key) for index in range(count)]
        else:
            series[key] = [_series_value(key, index, bucket_seconds, range_key) for index in range(count)]

    # The four host cards read their value labels from this block.
    profile = _host_profile()
    last_ram = series.get("ram_pct") or [40]
    last_vram = series.get("vram_pct") or [20]
    body["series"] = series
    body["system"] = {
        "ram_total_mb": profile["ram_total_mb"],
        "ram_used_mb": round(profile["ram_total_mb"] * last_ram[-1] / 100),
        "vram_total_mb": profile["vram_total_mb"],
        "vram_used_mb": round(profile["vram_total_mb"] * last_vram[-1] / 100),
    }

    totals = body.get("totals") or {}
    for key, values in series.items():
        if key in HOST_SERIES:
            continue
        if key in {"documents", "files"}:
            totals[key] = values[-1] if values else 0
        else:
            totals[key] = sum(values)
    body["totals"] = totals
    return body


def rewrite_for_publication(snapshot: dict, lang: str) -> dict:
    """Replace everything personal in the captured admin data with fictional values."""
    result = json.loads(json.dumps(snapshot))
    users = FICTIONAL_USERS[lang]

    for route in result.get("routes", []):
        if route.get("path") == "/admin/api/users":
            # Built from an explicit allowlist, never by copying the captured row:
            # the template carried a real `password_hash`, the Tavily key
            # timestamp and the row's own dates into a page anyone can download.
            rows = []
            for index, (login, name) in enumerate(users):
                rows.append(
                    {
                        "id": index + 1,
                        "login": login,
                        "name": name,
                        "is_active": True,
                        "is_admin": False,
                        "language": lang,
                        "service_class": 2,
                        "theme": "light",
                        "voice_gender": "male",
                        "response_style": "neutral",
                        "sessions_count": 3 + index,
                        "tasks_count": index,
                        "messages_count": 12 + index * 5,
                        "outgoing_tokens": 4200 + index * 900,
                        "incoming_tokens": 38000 + index * 7000,
                        "files_count": 2 + index,
                        "documents_count": 1 + index,
                        "api_keys_count": 0,
                        "storage_used": 1024 * 1024 * (5 + index),
                        "storage_limit_default_mb": 500,
                        "storage_limit_mb": 500,
                        "storage_limit_custom": False,
                        "web_requests_month": 0,
                        "has_tavily_key": False,
                        "slm_facts_count": 0,
                        "camera_permissions": [],
                        "created_at": "Mon, 05 Jan 2026 09:00:00 GMT",
                        "updated_at": "Mon, 05 Jan 2026 09:00:00 GMT",
                    }
                )
            route["body"] = rows
        elif str(route.get("path", "")).startswith("/admin/api/monitoring"):
            body = route.get("body") or {}
            range_key = str(route.get("path", "")).split("range=")[-1] or "24h"
            body = _synthetic_monitoring(body, range_key)
            body["users"] = [{"login": login} for login, _ in users]
            route["body"] = body
        elif route.get("path") == "/admin/api/cameras" and isinstance(route.get("body"), list):
            for room in route["body"]:
                if lang == "en" and isinstance(room, dict):
                    room["name_forms"] = CAMERA_ROOMS_EN.get(room.get("code"), [str(room.get("code") or "room")])
        elif route.get("path") == "/api/sessions" and isinstance(route.get("body"), list):
            # A freshly created account owns one empty conversation; it is not a
            # demo scenario and would sit in the list as «New session».
            route["body"] = [item for item in route["body"] if item.get("message_count")]
    result = _scrub(result)
    _drop_missing_assets(result)
    return result


def _drop_missing_assets(snapshot: dict) -> None:
    """Clear attachment references whose bytes were never captured.

    A task step can produce a picture that is deleted when the step is retried;
    the message keeps its path, the asset list does not, and the static page
    would render a broken image. Dropping the path leaves a text-only message.
    """
    # Asset keys are either "<session>/<file>" or "/api/documents/…"; message
    # paths are the bare relative form, so compare with the leading slash removed.
    known = {str(asset).lstrip("/") for asset in snapshot.get("assets", {})}

    def key(path: str) -> str:
        return path.lstrip("/")

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            path = value.get("file_path")
            if isinstance(path, str) and path and key(path) not in known:
                value["file_path"] = None
                value["file_name"] = None
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(snapshot.get("routes"))


# Any field whose name says "secret" is dropped, whatever route it comes from:
# a new admin endpoint must not be able to leak through the demo by accident.
SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api_key|apikey|private_key|credential|tavily)", re.IGNORECASE
)
# Values that look like a credential even under an innocent key.
SECRET_VALUE_RE = re.compile(
    r"(scrypt:|\$argon2|pbkdf2:sha256|tvly-[A-Za-z0-9]{8,}|flai_[A-Za-z0-9]{16,}"
    r"|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY)",
)


def _scrub(value: Any, key: str = "") -> Any:
    """Drop credential-shaped fields and values from captured data.

    Counters and flags are kept (`api_keys_count`, `has_tavily_key`) because the
    interface renders them; anything that could hold a real credential is not.
    """
    if isinstance(value, dict):
        cleaned: dict = {}
        for name, item in value.items():
            if SENSITIVE_KEY_RE.search(name) and not isinstance(item, (bool, int, float)):
                continue
            cleaned[name] = _scrub(item, name)
        return cleaned
    if isinstance(value, list):
        return [_scrub(item, key) for item in value]
    if isinstance(value, str) and SECRET_VALUE_RE.search(value):
        return ""
    return value


def _prune_orphans(snapshot: dict) -> dict:
    """Drop task/session detail routes and assets nothing in the lists points at.

    A partial re-run leaves the routes of tasks the capture deleted; the builder
    inlines every asset, so an orphan route bloats the published page (and its
    16 MB budget) without ever being opened.
    """
    routes = snapshot.get("routes", [])
    task_ids: set[str] = set()
    session_ids: set[str] = set()
    for route in routes:
        path = route.get("path", "")
        if path == "/api/tasks":
            body = route.get("body")
            items = body.get("tasks") if isinstance(body, dict) else body
            task_ids |= {str(item.get("id")) for item in items or [] if isinstance(item, dict)}
        elif path == "/api/sessions":
            items = route.get("body")
            session_ids |= {str(item.get("id")) for item in items or [] if isinstance(item, dict)}
    kept = []
    for route in routes:
        path = route.get("path", "")
        task_match = re.match(r"^/api/tasks/([^/]+)/messages$", path)
        if task_match and task_match.group(1) not in task_ids:
            continue
        session_match = re.match(r"^/api/sessions/([^/]+)/messages$", path)
        if session_match and session_match.group(1) not in session_ids:
            continue
        kept.append(route)
    blob = json.dumps(kept, ensure_ascii=False)
    assets = snapshot.get("assets") or {}
    snapshot["routes"] = kept
    snapshot["assets"] = {key: value for key, value in assets.items() if key in blob or value in blob}
    return snapshot


def _merge_partial(fresh: dict, lang: str, prompts: list[str]) -> dict:
    """Fold a partial run into the committed snapshot.

    Re-running one scenario must not erase the other sixteen. The fresh run owns
    the routes of the scenarios it captured plus the two global lists; every
    other route and asset comes from the previous snapshot, and the old copy of
    a re-run scenario is dropped by its prompt.
    """
    path = DEMO_DIR / "snapshot" / f"{lang}.json"
    if not path.is_file():
        return fresh
    old = json.loads(path.read_text(encoding="utf-8"))
    needles = [prompt[:60] for prompt in prompts]
    kept = [
        route
        for route in old.get("routes", [])
        if not any(needle in json.dumps(route.get("body"), ensure_ascii=False) for needle in needles)
    ]
    # The global lists are re-recorded by every run; an empty account (the demo
    # logins are wiped between releases) must not blank the captured lists.
    def _has_content(route: dict) -> bool:
        body = route.get("body")
        if route.get("path") == "/api/tasks":
            return bool(isinstance(body, dict) and body.get("tasks"))
        return bool(body)

    fresh_lists = [
        route
        for route in fresh.get("routes", [])
        if route.get("path") in ("/api/sessions", "/api/tasks") and _has_content(route)
    ]
    fresh_others = [
        route for route in fresh.get("routes", []) if route.get("path") not in ("/api/sessions", "/api/tasks")
    ]
    replaced_lists = {route["path"] for route in fresh_lists}
    kept = [route for route in kept if route.get("path") not in replaced_lists]
    merged = {**old}
    merged["routes"] = kept + fresh_others + fresh_lists
    merged["assets"] = {**old.get("assets", {}), **fresh.get("assets", {})}
    print(f"[{lang}] partial merge: {len(kept)} kept + {len(fresh.get('routes', []))} fresh routes", flush=True)
    return merged


def wait_for_completion(session: DemoSession, request_id: str, timeout_s: float = 900.0) -> dict:
    """Follow a queued task through its requeues until it completes."""
    deadline = time.monotonic() + timeout_s
    current = request_id
    while time.monotonic() < deadline:
        body = session.get_json(f"/api/queue/result/{current}")
        status = body.get("status")
        if status == "completed":
            return body.get("result") or {}
        if status == "error":
            raise CaptureError(f"Task {current} failed: {body.get('error')}")
        if status == "queued" and body.get("request_id"):
            current = body["request_id"]
        time.sleep(2.0)
    raise CaptureTimeoutError(f"Task {request_id} did not finish within {timeout_s} s")


def wait_for_idle(session: DemoSession, session_id: str, timeout_s: float = 1200.0) -> list:
    """Wait until the queue for this user is empty and the conversation stops changing.

    A routed request can be requeued as an image or video task, so the first
    "completed" result is not the end of the answer.
    """
    last: tuple | None = None
    stable = 0
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = session.get_json("/api/queue/status")
        busy = bool(status.get("processing")) or bool(status.get("queued"))
        messages = session.get_json(f"/api/sessions/{session_id}/messages?limit=100&offset=0")["messages"]
        signature = (len(messages), messages[-1].get("timestamp") if messages else None)
        stable = stable + 1 if signature == last and not busy else 0
        last = signature
        if stable >= 3 and messages and messages[-1].get("role") == "assistant":
            return messages
        time.sleep(2.0)
    raise CaptureTimeoutError(f"Session {session_id} did not settle within {timeout_s} s")


def _record(routes: list[dict], method: str, path: str, body: Any) -> None:
    for route in routes:
        if route["method"] == method and route["path"] == path:
            route["body"] = body
            return
    routes.append({"method": method, "path": path, "status": 200, "body": body})


def _scenario_paths(session_id: str) -> list[str]:
    return [
        f"/api/sessions/{session_id}/messages?limit=100&offset=0",
        "/api/sessions",
        "/api/tasks",
        "/api/queue/status",
        "/api/queue/counts",
        "/api/tasks/queue-count",
    ]


def _media_paths(payload: Any, collected: set[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if (
                key == "file_path"
                and isinstance(value, str)
                and value
                and not value.lower().endswith(".pdf")
                and not value.startswith(ACCOUNT_PREFIX)
            ):
                collected.add(value)
            elif isinstance(value, str) and '"file_path"' in value:
                # Task steps and message parts keep their attachments inside a
                # JSON string; the walker used to miss every task artifact.
                try:
                    _media_paths(json.loads(value), collected)
                except ValueError:
                    for match in FILE_PATH_RE.findall(value):
                        if match and not match.lower().endswith(".pdf"):
                            collected.add(match)
            else:
                _media_paths(value, collected)
    elif isinstance(payload, list):
        for item in payload:
            _media_paths(item, collected)


PHOTO_PROMPT = {
    "ru": "Сделай фото: тарелка с яблоками и виноградом на деревянном столе у окна. Без упаковок и надписей.",
    "en": "Make a photo: a plate of apples and grapes on a wooden table by the window. No packaging, no labels.",
}

VOICE_TEXT = {
    "ru": "что поставят на стол для осенней ярмарки в школе",
    "en": "what will be served at the table for the school autumn fair",
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".avi"}
FILE_PATH_RE = re.compile(r'"file_path"\s*:\s*"([^"]+)"')

IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

TEXT_CHECK_PROMPT = {
    "ru": (
        "Посмотри на изображение. Есть ли на нём ЛЮБЫЕ буквы, слова, надписи, цифры "
        "или текстовые логотипы? Не описывай картинку. Ответь строго одним словом: ДА или НЕТ."
    ),
    "en": (
        "Look at the image. Does it contain ANY letters, words, captions, numbers or text logos? "
        "Do not describe the picture. Answer with exactly one word: YES or NO."
    ),
}

TEXT_ANSWER_RE = re.compile(r"\b(да|нет|yes|no)\b", re.IGNORECASE)


def _message_text(message: dict) -> str:
    """Return the plain text of a stored message, whatever shape its content has."""
    content = message.get("content") or ""
    if isinstance(content, str) and content.lstrip().startswith("["):
        try:
            parts = json.loads(content)
        except json.JSONDecodeError:
            return content
        return " ".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    return str(content)


def text_found(answer: str) -> bool:
    """Read the yes/no verdict of the image check; an unclear answer counts as a defect."""
    match = TEXT_ANSWER_RE.search(answer or "")
    if not match:
        return True
    return match.group(1).lower() in {"да", "yes"}


def check_image_text(session: DemoSession, file_path: str, lang: str) -> tuple[bool, str]:
    """Ask the product's own multimodal model whether a generated picture shows any text."""
    try:
        payload = session.get_bytes(f"/api/files/{file_path}")
    except CaptureError:
        print(f"skip missing image: {file_path}", flush=True)
        return False, ""
    check_id = session.post_json("/api/sessions/new", {})["id"]
    try:
        session.post_form(
            "/api/send_message",
            data={"message": TEXT_CHECK_PROMPT[lang], "session_id": check_id},
            files={"file": (Path(file_path).name, payload, IMAGE_MIME.get(Path(file_path).suffix.lower(), "image/png"))},
        )
        messages = wait_for_idle(session, check_id, timeout_s=600.0)
        answer = _message_text(messages[-1])
        return text_found(answer), answer
    finally:
        with contextlib.suppress(CaptureError):
            session.post_json(f"/api/sessions/{check_id}/delete", {})


def _image_paths(payload: Any) -> list[str]:
    """Collect every image referenced by a payload, in order, without duplicates."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "file_path" and isinstance(item, str) and Path(item).suffix.lower() in IMAGE_SUFFIXES:
                    if item not in found:
                        found.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return found


def _task_image_paths(payload: Any) -> list[str]:
    """Pictures a task actually shows: the accepted step media and the final results.

    Other paths in a task message are internal references — a repair source, for
    example, is deleted once the repair is accepted and must not be fetched.
    """
    found: list[str] = []
    for message in payload.get("messages") or []:
        extra = message.get("extra") or {}
        media = extra.get("media")
        if isinstance(media, dict):
            media = [media]
        candidates = [item.get("file_path") for item in media or [] if isinstance(item, dict)]
        candidates += [item.get("file_path") for item in extra.get("final_items") or [] if isinstance(item, dict)]
        for path in candidates:
            if isinstance(path, str) and Path(path).suffix.lower() in IMAGE_SUFFIXES and path not in found:
                found.append(path)
    return found


def media_rejection_defects(payload: dict) -> int:
    """Delivered media whose every verification attempt failed.

    A task keeps the closest candidate after its attempts run out ("best
    effort"); that artifact is known to be wrong and must not reach the demo.
    """
    defects = 0
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        extra = message.get("extra") or {}
        if not extra.get("media"):
            continue
        attempts = [a for a in (extra.get("attempts_log") or []) if isinstance(a, dict)]
        if extra.get("best_effort") or (attempts and not any(a.get("ok") for a in attempts)):
            defects += 1
    return defects


def _has_video(session: DemoSession, session_id: str, task_id: str, lang: str) -> bool:
    """True when the scenario produced a clip (chat attachment or task artifact)."""
    paths: set[str] = set()
    if session_id:
        _media_paths(session.get_json(f"/api/sessions/{session_id}/messages?limit=100&offset=0"), paths)
    if task_id:
        _media_paths(session.get_json(f"/api/tasks/{task_id}/messages"), paths)
    return any(str(path).lower().endswith(tuple(VIDEO_SUFFIXES)) for path in paths)


def scenario_images(session: DemoSession, session_id: str, task_id: str) -> list[str]:
    """Every picture a scenario produced: chat attachments and task artifacts."""
    images: list[str] = []
    if session_id:
        messages = session.get_json(f"/api/sessions/{session_id}/messages?limit=100&offset=0")
        images.extend(_image_paths(messages))
    if task_id:
        messages = session.get_json(f"/api/tasks/{task_id}/messages")
        for path in _task_image_paths(messages):
            if path not in images:
                images.append(path)
    return images


def _latest_image(payload: Any) -> str:
    found = ""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "file_path" and isinstance(value, str) and Path(value).suffix.lower() in IMAGE_SUFFIXES:
                found = value
            else:
                candidate = _latest_image(value)
                if candidate:
                    found = candidate
    elif isinstance(payload, list):
        for item in payload:
            candidate = _latest_image(item)
            if candidate:
                found = candidate
    return found


def prepare_photo(session: DemoSession, session_id: str, lang: str) -> Path:
    """Generate the input photo with the product itself and keep it for the attachment scenario."""
    session.post_json("/api/send_message", {"message": PHOTO_PROMPT[lang], "session_id": session_id})

    def find_image() -> str:
        messages = session.get_json(f"/api/sessions/{session_id}/messages?limit=100&offset=0")
        return _latest_image(messages)

    found: dict = {}

    def has_image() -> bool:
        path = find_image()
        if path:
            found["path"] = path
            return True
        return False

    wait_for(has_image, timeout_s=900.0, interval_s=5.0)
    file_path = found["path"]
    target = _input_dir(lang) / f"photo{Path(file_path).suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(session.get_bytes(f"/api/files/{file_path}"))
    return target


def prepare_voice(session: DemoSession, lang: str) -> Path:
    """Synthesize the voice message with the product TTS."""
    content, content_type = session.post_raw(
        "/api/tts/synthesize", {"text": VOICE_TEXT[lang], "lang": lang}
    )
    suffix = ".wav" if "wav" in content_type else ".mp3"
    target = _input_dir(lang) / f"voice{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _save_generated_image(session: DemoSession, file_path: str, lang: str) -> Path:
    """Download a generated picture so the next scenario can attach it."""
    suffix = Path(file_path).suffix.lower() or ".png"
    target = _input_dir(lang) / f"previous{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(session.get_bytes(f"/api/files/{file_path}"))
    return target


def set_video_backend(admin: DemoSession, backend: str) -> None:
    """Select the video backend through the admin settings API.

    ``VIDEO_BACKEND`` is a saved setting, so the change applies without a
    restart and ``VideoModule`` picks it up on the next request. Resetting the
    key restores the value from `.env`.
    """
    if backend == "wan":
        admin.post_json("/admin/api/settings", {"values": {"VIDEO_BACKEND": "wan"}})
    else:
        admin.post_json("/admin/api/settings/reset", {"keys": ["VIDEO_BACKEND"]})
    print(f"[settings] VIDEO_BACKEND={backend}", flush=True)


def _attachment_files(scenario: dict, prepared: dict, lang: str) -> dict:
    attach = scenario.get("attach")
    if not attach:
        return {}
    if attach == "generated-image":
        return {"file": (prepared["photo"].name, prepared["photo"].read_bytes(), "image/png")}
    if attach == "previous-image" and prepared.get("force_attach"):
        path = Path(prepared["force_attach"])
        if path.is_file():
            mime = IMAGE_MIME.get(path.suffix.lower(), "image/png")
            return {"file": (path.name, path.read_bytes(), mime)}
    if attach == "previous-image":
        # The picture the previous scenario produced (e.g. video from the image
        # drawn one step earlier), downloaded and attached like a user would.
        path = prepared.get("previous_image")
        if path is None or not Path(path).is_file():
            # The image scenario can fail to return a picture; the run must not
            # die on the missing attachment — fall back to the prepared photo,
            # or send the request without one.
            path = prepared.get("photo")
            if path is None or not Path(path).is_file():
                print(f"[{lang}] no picture to attach for '{scenario.get('title')}'", flush=True)
                return {}
            print(f"[{lang}] previous picture missing — attaching the prepared photo", flush=True)
        mime = IMAGE_MIME.get(path.suffix.lower(), "image/png")
        return {"file": (path.name, path.read_bytes(), mime)}
    if attach == "tts":
        mime = "audio/wav" if prepared["voice"].suffix == ".wav" else "audio/mpeg"
        return {"voice": (prepared["voice"].name, prepared["voice"].read_bytes(), mime)}
    raise CaptureError(f"Unknown attachment: {attach}")


def _run_scenario(
    session: DemoSession,
    session_id: str,
    scenario: dict,
    document_ids: list,
    lang: str,
    prepared: dict,
) -> dict:
    kind = scenario.get("kind", "text")
    prompt = scenario["prompt"]

    if kind in {"text", "image", "video"}:
        if scenario.get("attach") == "generated-image":
            prepared["photo"] = prepare_photo(session, session_id, lang)
        files = _attachment_files(scenario, prepared, lang)
        data = {"message": prompt, "session_id": session_id}
        if "voice" in files:
            data["voice_record"] = "true"
        if files:
            session.post_form("/api/send_message", data=data, files=files)
        else:
            session.post_json("/api/send_message", data)
        wait_for_idle(session, session_id)
    elif kind == "rlm":
        response = session.post_json(
            "/api/rlm/analyze",
            {"session_id": session_id, "doc_ids": document_ids, "question": prompt},
        )
        wait_for_completion(session, response["task_id"])
        wait_for_idle(session, session_id)
    elif kind == "task":
        created = session.post_json("/api/tasks", {})
        task_id = created["task"]["id"]
        payload = {"request": prompt}
        if scenario.get("deep_analysis"):
            payload["deep_analysis"] = True
            payload["doc_ids"] = document_ids
        session.post_json(f"/api/tasks/{task_id}/request", payload)
        if scenario.get("interrupt"):
            time.sleep(45)
            session.post_json(
                "/api/send_message",
                {"message": scenario.get("interrupt_text", prompt), "session_id": session_id},
            )
            time.sleep(20)
            return {"task_id": task_id, "interrupted": True}
        try:
            wait_for(
                lambda: session.get_json(f"/api/tasks/{task_id}/messages")["task"]["status"]
                in {"completed", "failed", "cancelled", "awaiting_user", "expired"},
                # A task that generates several artifacts (two images and two
                # videos) spends most of an hour inside the queue and the
                # verifier; 40 minutes cut a healthy run short.
                timeout_s=5400.0,
                interval_s=10.0,
            )
        except CaptureTimeoutError:
            # Keep the task and carry on: it may still finish before the run
            # records /api/tasks at the end, and losing the remaining scenarios
            # over one slow task is worse than showing it mid-flight.
            print(f"[task] {scenario.get('title')} did not finish in time — keeping it as is", flush=True)
        return {"task_id": task_id}
    else:
        raise CaptureError(f"Unknown scenario kind: {kind}")
    return {}


def capture_language(
    base_url: str,
    lang: str,
    *,
    password: str,
    force: bool = False,
    transport: Any = None,
    timeout_s: float = 900.0,
    only: list[str] | None = None,
    attach_file: str = "",
    skip_documents: bool = False,
) -> dict:
    """Drive the running instance through the demo scenarios and return the snapshot."""
    if not force:
        raise CaptureError("Refusing to run without --force: the capture writes into the live database")

    scenarios_path = DEMO_DIR / "scenarios" / f"{lang}.json"
    if not scenarios_path.is_file():
        raise CaptureError(f"Scenario file is missing: {scenarios_path}")
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8"))
    if only:
        wanted = {w.strip().lower() for w in only}
        kept = []
        for index, item in enumerate(scenarios["scenarios"], 1):
            title = str(item.get("title", "")).strip().lower()
            if str(index) in wanted or any(w and w in title for w in wanted):
                kept.append(item)
        if not kept:
            raise CaptureError(f"no scenario matches: {', '.join(only)}")
        print(f"[{lang}] partial capture: {len(kept)} of {len(scenarios['scenarios'])} scenarios", flush=True)
        scenarios = {**scenarios, "scenarios": kept}

    chat = DemoSession(base_url, transport=transport)
    chat.login(f"{ACCOUNT_PREFIX}{lang}_chat", password)
    admin = DemoSession(base_url, transport=transport)
    admin.login(f"{ACCOUNT_PREFIX}{lang}_admin", password)

    routes: list[dict] = []
    sessions = [session for session in chat.get_json("/api/sessions") if session.get("message_count")]
    _record(routes, "GET", "/api/sessions", sessions)

    document_ids: list[str] = []
    if not skip_documents:
        for document in scenarios["documents"]:
            print(f"[{lang}] upload: {document['name']}", flush=True)
            source = DEMO_DIR / document["source"]
            with source.open("rb") as handle:
                uploaded = chat.post_form(
                    "/api/documents/upload",
                    files={"file": (document["name"], handle, "application/pdf")},
                )
            document_ids.append(uploaded["id"])

        wait_for(
            lambda: all(
                doc.get("index_status") == "indexed"
                for doc in chat.get_json("/api/documents")["documents"]
                if doc["id"] in document_ids
            ),
            timeout_s=900.0,
            interval_s=5.0,
        )
    _record(routes, "GET", "/api/documents", chat.get_json("/api/documents"))
    _record(routes, "GET", "/api/tasks", chat.get_json("/api/tasks"))

    prepared: dict = {}
    if any(scenario.get("attach") == "tts" for scenario in scenarios["scenarios"]):
        prepared["voice"] = prepare_voice(chat, lang)

    media: dict[str, str] = {}
    unfinished: dict[str, dict] = {}
    if attach_file:
        prepared["force_attach"] = attach_file
    current_backend = "ltx"
    for scenario in scenarios["scenarios"]:
        desired_backend = scenario.get("video_backend", "ltx")
        if desired_backend != current_backend:
            set_video_backend(admin, desired_backend)
            current_backend = desired_backend
        # A background task is not a conversation: creating a chat session for it
        # leaves an empty "New session" entry in the demo session list.
        needs_session = scenario.get("kind") != "task" or scenario.get("interrupt")
        session_id = ""
        for attempt in range(1, 4):
            session_id = chat.post_json("/api/sessions/new", {})["id"] if needs_session else ""
            attempt_note = f" (attempt {attempt})" if attempt > 1 else ""
            print(f"[{lang}] {scenario['kind']}: {scenario['title']}{attempt_note}", flush=True)
            result = _run_scenario(chat, session_id, scenario, document_ids, lang, prepared)
            task_id = result.get("task_id", "")
            images = scenario_images(chat, session_id, task_id)
            defects = 0
            if task_id:
                rejected = media_rejection_defects(chat.get_json(f"/api/tasks/{task_id}/messages"))
                if rejected:
                    print(f"[{lang}] {rejected} artifact(s) rejected by the verifier", flush=True)
                defects += rejected
            if not images and scenario.get("kind") == "image":
                # The router occasionally answers an image request with text;
                # without a picture the scenario has nothing to show.
                print(f"[{lang}] no picture produced", flush=True)
                defects += 1
            if scenario.get("kind") == "video" and not _has_video(chat, session_id, task_id, lang):
                # Same for video: an answer without a clip means the router or
                # the pipeline dropped it, and the page would show text only.
                print(f"[{lang}] no video produced", flush=True)
                defects += 1
            # A video scenario attaches a *source* picture; OCRing the user's own
            # photo is not a defect check (it used to fail every attempt and the
            # retry threw away the clip). Only the produced clip matters there.
            checkable = [] if scenario.get("kind") == "video" else images
            for image in checkable:
                found, answer = check_image_text(chat, image, lang)
                verdict = "TEXT" if found else "clean"
                print(f"[{lang}]   image {Path(image).name}: {verdict} — {answer[:80]}", flush=True)
                defects += 1 if found else 0
            if images and scenario.get("kind") == "image":
                # Keep it for the next scenario that attaches the previous picture
                # (video from the image drawn one step earlier).
                prepared["previous_image"] = _save_generated_image(chat, images[-1], lang)
            if not defects:
                if result.get("interrupted"):
                    unfinished[task_id] = scenario
                break
            print(f"[{lang}] retry {scenario['title']}: {defects} defect(s) in {len(images)} image(s)", flush=True)
            if attempt == 3:
                # Keep the last attempt: the demo must still show something, and the
                # log has already reported that none of the pictures came out clean.
                print(f"[{lang}] keeping the last attempt of {scenario['title']}", flush=True)
                break
            if "task_id" in result:
                chat.post_json(f"/api/tasks/{task_id}/delete", {})
            else:
                chat.post_json(f"/api/sessions/{session_id}/delete", {})
        if result.get("interrupted") and session_id:
            # The interruption message leaves a conversation with a question and no
            # answer: it exists only to preempt the task, not to be shown.
            chat.post_json(f"/api/sessions/{session_id}/delete", {})
            session_id = ""
        if session_id:
            for path in _scenario_paths(session_id):
                _record(routes, "GET", path, chat.get_json(path))

    if current_backend != "ltx":
        set_video_backend(admin, "ltx")

    for task_id, scenario in unfinished.items():
        body = chat.get_json(f"/api/tasks/{task_id}/messages")["task"]
        if body["status"] not in {"completed", "failed", "cancelled", "awaiting_user", "expired"}:
            continue
        retry = dict(scenario)
        retry.pop("interrupt", None)
        for _attempt in range(1, 4):
            images = scenario_images(chat, "", task_id)
            defects = 0
            for image in images:
                found, answer = check_image_text(chat, image, lang)
                print(f"[{lang}]   image {Path(image).name}: {'TEXT' if found else 'clean'} — {answer[:80]}", flush=True)
                defects += 1 if found else 0
            if not defects:
                break
            print(f"[{lang}] retry {scenario['title']}: {defects} defect(s) in {len(images)} image(s)", flush=True)
            if _attempt == 3:
                print(f"[{lang}] keeping the last attempt of {scenario['title']}", flush=True)
                break
            chat.post_json(f"/api/tasks/{task_id}/delete", {})
            task_id = _run_scenario(chat, "", retry, document_ids, lang, prepared)["task_id"]

    # The last scenarios are background tasks and own no chat session, so the
    # lists recorded per scenario are stale by the end of the run.
    _record(routes, "GET", "/api/sessions", [s for s in chat.get_json("/api/sessions") if s.get("message_count")])
    _record(routes, "GET", "/api/tasks", chat.get_json("/api/tasks"))

    for task in chat.get_json("/api/tasks")["tasks"]:
        _record(routes, "GET", f"/api/tasks/{task['id']}/messages", chat.get_json(f"/api/tasks/{task['id']}/messages"))

    collected: set[str] = set()
    for route in routes:
        _media_paths(route["body"], collected)
    media_dir = DEMO_DIR / "media" / lang
    media_dir.mkdir(parents=True, exist_ok=True)
    for file_path in sorted(collected):
        payload_bytes = None
        for _attempt in range(3):
            try:
                payload_bytes = chat.get_bytes(f"/api/files/{file_path}")
                break
            except CaptureError:
                time.sleep(3)
        if payload_bytes is None:
            print(f"[{lang}] skip missing media: {file_path}", flush=True)
            continue
        target = media_dir / Path(file_path).name
        target.write_bytes(payload_bytes)
        media[file_path] = f"media/{lang}/{target.name}"

    for document in chat.get_json("/api/documents")["documents"]:
        preview = chat.get_json(f"/api/documents/{document['id']}/preview")
        _record(routes, "GET", f"/api/documents/{document['id']}/preview", preview)
        if preview.get("kind") != "pdf":
            continue
        for page in range(1, int(preview.get("pages") or 1) + 1):
            page_path = f"{preview['page_url']}{page}"
            name = f"preview-{document['id'][:8]}-{page}.png"
            (media_dir / name).write_bytes(chat.get_bytes(page_path))
            media[page_path] = f"media/{lang}/{name}"

    compress_images(media)

    snapshot = {
        "lang": lang,
        "credentials": {
            "user": {"login": "user", "password": "user"},
            "admin": {"login": "admin", "password": "admin"},
        },
        "routes": routes,
        "assets": media,
    }
    snapshot["routes"].extend(capture_admin(admin)["routes"])
    snapshot = rewrite_for_publication(snapshot, lang)
    if only:
        snapshot = _merge_partial(snapshot, lang, [sc["prompt"] for sc in scenarios["scenarios"]])
    snapshot = _prune_orphans(snapshot)
    save_snapshot(DEMO_DIR / "snapshot" / f"{lang}.json", snapshot)

    manifest_path = _manifest_path(lang)
    write_manifest(
        manifest_path,
        {
            "accounts": [f"{ACCOUNT_PREFIX}{lang}_chat", f"{ACCOUNT_PREFIX}{lang}_admin"],
            "media": sorted(media.values()),
        },
    )
    print(f"manifest: {manifest_path}")
    return snapshot





def compress_images(media: dict[str, str], base_dir: Path | None = None) -> None:
    """Store pictures as WebP: the raw PNGs would push a page over its size limit."""
    from PIL import Image

    base = base_dir or DEMO_DIR
    for path, relative in list(media.items()):
        target = base / relative
        if target.suffix.lower() not in {".png", ".jpg", ".jpeg"} or not target.is_file():
            continue
        webp = target.with_suffix(".webp")
        with Image.open(target) as image:
            image.save(webp, format="WEBP", quality=82, method=6)
        target.unlink()
        media[path] = str(Path(relative).with_suffix(".webp"))


def _input_dir(lang: str) -> Path:
    import tempfile

    path = Path(tempfile.gettempdir()) / f"flai-demo-{lang}-input"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest_path(lang: str) -> Path:
    import tempfile

    return Path(tempfile.gettempdir()) / f"flai-demo-{lang}.manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo.capture")
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--lang", choices=("ru", "en", "all"), default="all")
    parser.add_argument("--password", required=True, help="password of the demo accounts")
    parser.add_argument("--force", action="store_true", help="confirm writing into the live database")
    parser.add_argument(
        "--attach-file",
        default="",
        help="image to attach to a re-captured scenario that normally takes the "
        "previous picture (partial runs have no previous scenario)",
    )
    parser.add_argument(
        "--only",
        default="",
        help="re-capture only these scenarios (comma-separated titles or indexes); "
        "the rest of the snapshot is kept",
    )
    parser.add_argument(
        "--skip-documents",
        action="store_true",
        help="do not upload or index the scenario documents (a targeted media re-run)",
    )
    parser.add_argument("--cleanup", default="", help="finish the removal of an interrupted capture")
    parser.add_argument("--database-url", default="", help="delete the orphan background tasks")
    args = parser.parse_args(argv)

    if args.cleanup:
        result = cleanup(args.base_url, Path(args.cleanup), database_url=args.database_url)
        print(f"deleted: {', '.join(result['deleted'])}; residue: {result['residue']}")
        return 0

    languages = ("ru", "en") if args.lang == "all" else (args.lang,)
    for lang in languages:
        only = [item for item in args.only.split(",") if item.strip()]
        snapshot = capture_language(
            args.base_url,
            lang,
            password=args.password,
            force=args.force,
            only=only or None,
            attach_file=args.attach_file,
            skip_documents=args.skip_documents,
        )
        print(f"{lang}: {len(snapshot['routes'])} routes, {len(snapshot['assets'])} assets")
    return 0


ADMIN_PATHS = (
    "/admin/api/hardware",
    "/admin/api/model_configs",
    "/admin/api/llamacpp/models?list_type=gguf_files",
    "/admin/api/hub/installed",
    "/admin/api/users",
    "/admin/api/stats",
    "/admin/api/backups/",
    "/admin/api/cameras",
    "/admin/api/branding",
    "/admin/api/settings",
)

MONITORING_RANGES = ("1h", "6h", "24h", "7d", "30d")

DELETION_TABLES = ("tasks", "task_steps")


def capture_admin(session: DemoSession) -> dict:
    """Record every admin endpoint the panel reads."""
    routes: list[dict] = []
    for path in ADMIN_PATHS:
        _record(routes, "GET", path, session.get_json(path))
    for range_key in MONITORING_RANGES:
        path = f"/admin/api/monitoring?range={range_key}"
        _record(routes, "GET", path, session.get_json(path))
    return {"routes": routes}


def write_manifest(path: Path, manifest: dict) -> None:
    """Persist what a capture created, so an interrupted run can be cleaned up."""
    save_snapshot(path, manifest)


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cleanup_plan(manifest: dict) -> dict:
    """Describe every residue a capture leaves behind."""
    logins = list(manifest.get("accounts", []))
    return {
        "logins": logins,
        "tables": list(DELETION_TABLES),
        "folders": logins,
        "media": list(manifest.get("media", [])),
    }


def cleanup(base_url: str, manifest_path: Path, *, database_url: str = "") -> dict:
    """Delete the demo accounts and report anything the product's deletion path leaves behind."""
    manifest = load_manifest(manifest_path)
    plan = cleanup_plan(manifest)
    session = DemoSession(base_url)
    session.login(plan["logins"][-1], manifest.get("password", ""))
    for login in plan["logins"]:
        session._request("DELETE", f"/admin/api/users/{login}")
    residue = {"tasks": 0}
    if database_url:
        import psycopg2

        with psycopg2.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM task_steps WHERE task_id IN (SELECT id FROM tasks WHERE user_id = ANY(%s))", (plan["logins"],))
            cursor.execute("DELETE FROM tasks WHERE user_id = ANY(%s)", (plan["logins"],))
            residue["tasks"] = cursor.rowcount
    return {"deleted": plan["logins"], "residue": residue}


if __name__ == "__main__":
    raise SystemExit(main())
