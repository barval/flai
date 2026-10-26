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


def _synthetic_monitoring(body: dict, range_key: str) -> dict:
    """Give every monitoring range plausible, distinct numbers."""
    count = len(body.get("buckets") or []) or 24
    bucket_seconds = int(body.get("bucket_seconds") or 3600)
    series = body.get("series") or {}
    for key in series:
        series[key] = [_series_value(key, index, bucket_seconds, range_key) for index in range(count)]
    body["series"] = series
    totals = body.get("totals") or {}
    for key, values in series.items():
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
            template = (route.get("body") or [{}])[0] or {}
            rows = []
            for index, (login, name) in enumerate(users):
                row = dict(template)
                row.update(
                    {
                        "login": login,
                        "name": name,
                        "is_active": True,
                        "service_class": 2,
                        "sessions_count": 3 + index,
                        "tasks_count": index,
                        "messages_count": 12 + index * 5,
                        "outgoing_tokens": 4200 + index * 900,
                        "incoming_tokens": 38000 + index * 7000,
                        "files_count": 2 + index,
                        "documents_count": 1 + index,
                        "api_keys_count": 0,
                        "storage_used": 1024 * 1024 * (5 + index),
                        "web_requests_month": 0,
                        "has_tavily_key": False,
                        "slm_facts_count": 0,
                        "camera_permissions": [],
                    }
                )
                rows.append(row)
            route["body"] = rows
        elif str(route.get("path", "")).startswith("/admin/api/monitoring"):
            body = route.get("body") or {}
            range_key = str(route.get("path", "")).split("range=")[-1] or "24h"
            body = _synthetic_monitoring(body, range_key)
            body["users"] = [{"login": login} for login, _ in users]
            route["body"] = body
        elif route.get("path") == "/api/sessions" and isinstance(route.get("body"), list):
            # A freshly created account owns one empty conversation; it is not a
            # demo scenario and would sit in the list as «New session».
            route["body"] = [item for item in route["body"] if item.get("message_count")]
    return result


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
    "ru": "Подскажи, сколько будет стоить партия из сорока ноутбуков, если один ноутбук стоит пятьдесят четыре тысячи девятьсот рублей.",
    "en": "Could you tell me how much a batch of forty laptops costs if one laptop is eight hundred and ninety nine pounds?",
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

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


def _attachment_files(scenario: dict, prepared: dict, lang: str) -> dict:
    attach = scenario.get("attach")
    if not attach:
        return {}
    if attach == "generated-image":
        return {"file": (prepared["photo"].name, prepared["photo"].read_bytes(), "image/png")}
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
        wait_for(
            lambda: session.get_json(f"/api/tasks/{task_id}/messages")["task"]["status"]
            in {"completed", "failed", "cancelled", "awaiting_user", "expired"},
            timeout_s=2400.0,
            interval_s=10.0,
        )
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
) -> dict:
    """Drive the running instance through the demo scenarios and return the snapshot."""
    if not force:
        raise CaptureError("Refusing to run without --force: the capture writes into the live database")

    scenarios_path = DEMO_DIR / "scenarios" / f"{lang}.json"
    if not scenarios_path.is_file():
        raise CaptureError(f"Scenario file is missing: {scenarios_path}")
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8"))

    chat = DemoSession(base_url, transport=transport)
    chat.login(f"{ACCOUNT_PREFIX}{lang}_chat", password)
    admin = DemoSession(base_url, transport=transport)
    admin.login(f"{ACCOUNT_PREFIX}{lang}_admin", password)

    routes: list[dict] = []
    sessions = [session for session in chat.get_json("/api/sessions") if session.get("message_count")]
    _record(routes, "GET", "/api/sessions", sessions)

    document_ids: list[str] = []
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
    for scenario in scenarios["scenarios"]:
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
            for image in images:
                found, answer = check_image_text(chat, image, lang)
                verdict = "TEXT" if found else "clean"
                print(f"[{lang}]   image {Path(image).name}: {verdict} — {answer[:80]}", flush=True)
                defects += 1 if found else 0
            if not defects:
                if result.get("interrupted"):
                    unfinished[task_id] = scenario
                break
            print(f"[{lang}] retry {scenario['title']}: text on {defects} of {len(images)} image(s)", flush=True)
            if attempt == 3:
                # Keep the last attempt: the demo must still show something, and the
                # log has already reported that none of the pictures came out clean.
                print(f"[{lang}] keeping the last attempt of {scenario['title']}", flush=True)
                break
            if "task_id" in result:
                chat.post_json(f"/api/tasks/{task_id}/delete", {})
            else:
                chat.post_json(f"/api/sessions/{session_id}/delete", {})
        if session_id:
            for path in _scenario_paths(session_id):
                _record(routes, "GET", path, chat.get_json(path))

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
            print(f"[{lang}] retry {scenario['title']}: text on {defects} of {len(images)} image(s)", flush=True)
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
    parser.add_argument("--cleanup", default="", help="finish the removal of an interrupted capture")
    parser.add_argument("--database-url", default="", help="delete the orphan background tasks")
    args = parser.parse_args(argv)

    if args.cleanup:
        result = cleanup(args.base_url, Path(args.cleanup), database_url=args.database_url)
        print(f"deleted: {', '.join(result['deleted'])}; residue: {result['residue']}")
        return 0

    languages = ("ru", "en") if args.lang == "all" else (args.lang,)
    for lang in languages:
        snapshot = capture_language(args.base_url, lang, password=args.password, force=args.force)
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
