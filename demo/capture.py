"""Capture the demo snapshot from a running instance (release preparation only)."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from demo.io import DEMO_DIR, save_snapshot

CSRF_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)"')
ACCOUNT_PREFIX = "demo_"

FICTIONAL_USERS = {
    "ru": [("anna", "Анна"), ("igor", "Игорь"), ("maria", "Мария")],
    "en": [("alex", "Alex"), ("priya", "Priya"), ("tom", "Tom")],
}


class CaptureError(RuntimeError):
    """Raised when the capture cannot continue."""


class CaptureTimeout(CaptureError):
    """Raised when a queued task does not finish in time."""


def wait_for(predicate: Callable[[], bool], timeout_s: float, interval_s: float = 2.0) -> bool:
    """Poll ``predicate`` until it is true or the timeout expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    raise CaptureTimeout(f"Condition not met within {timeout_s} s")


class DemoSession:
    """Minimal HTTP client for the running instance: login, CSRF, JSON and multipart."""

    def __init__(self, base_url: str, transport: Any = None):
        if transport is None:
            import requests

            transport = requests.Session()
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.csrf_token = ""

    def _request(self, method: str, path: str, **kwargs):
        response = self.transport.request(method, f"{self.base_url}{path}", **kwargs)
        if response.status_code >= 400:
            raise CaptureError(f"{method} {path} failed with {response.status_code}: {response.text[:200]}")
        return response

    def login(self, login: str, password: str) -> None:
        self._request("POST", "/login", data={"login": login, "password": password, "theme": "light"})
        html = self._request("GET", "/chat").text
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
            files=files,
            headers={"X-CSRFToken": self.csrf_token},
        ).json()

    def get_bytes(self, path: str) -> bytes:
        return self._request("GET", path).content


def _synthetic_series(length: int, seed: int) -> list[int]:
    return [(seed + index * 7) % 11 * (index % 3 + 1) for index in range(length)]


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
        elif route.get("path") == "/admin/api/monitoring":
            body = route.get("body") or {}
            series = body.get("series") or {}
            for key, values in list(series.items()):
                series[key] = _synthetic_series(len(values) or 24, len(key))
            body["series"] = series
            body["users"] = [{"login": login} for login, _ in users]
            route["body"] = body
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
    raise CaptureTimeout(f"Task {request_id} did not finish within {timeout_s} s")


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
    ]


def _media_paths(payload: Any, collected: set[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "file_path" and isinstance(value, str) and value:
                collected.add(value)
            else:
                _media_paths(value, collected)
    elif isinstance(payload, list):
        for item in payload:
            _media_paths(item, collected)


def _run_scenario(session: DemoSession, session_id: str, scenario: dict, document_ids: list) -> None:
    kind = scenario.get("kind", "text")
    prompt = scenario["prompt"]

    if kind in {"text", "image", "video"}:
        response = session.post_form(
            "/api/send_message", data={"message": prompt, "session_id": session_id}
        )
        wait_for_completion(session, response["request_id"])
    elif kind == "rlm":
        response = session.post_form(
            "/api/rlm/analyze",
            data={
                "session_id": session_id,
                "doc_ids": json.dumps(document_ids),
                "text": prompt,
            },
        )
        wait_for_completion(session, response["task_id"])
    elif kind == "task":
        created = session.post_json("/api/tasks", {})
        task_id = created["task"]["id"]
        session.post_form(f"/api/tasks/{task_id}/request", data={"request": prompt})
        wait_for(
            lambda: session.get_json(f"/api/tasks/{task_id}")["task"]["status"]
            in {"completed", "failed", "cancelled"},
            timeout_s=1800.0,
            interval_s=10.0,
        )
    else:
        raise CaptureError(f"Unknown scenario kind: {kind}")


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
    _record(routes, "GET", "/api/sessions", chat.get_json("/api/sessions"))

    document_ids: list[str] = []
    for document in scenarios["documents"]:
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

    media: dict[str, str] = {}
    for scenario in scenarios["scenarios"]:
        session_id = chat.post_json("/api/sessions/new", {})["id"]
        _run_scenario(chat, session_id, scenario, document_ids)
        for path in _scenario_paths(session_id):
            _record(routes, "GET", path, chat.get_json(path))

    collected: set[str] = set()
    for route in routes:
        _media_paths(route["body"], collected)
    media_dir = DEMO_DIR / "media" / lang
    media_dir.mkdir(parents=True, exist_ok=True)
    for file_path in sorted(collected):
        target = media_dir / Path(file_path).name
        target.write_bytes(chat.get_bytes(f"/api/files/{file_path}"))
        media[file_path] = f"media/{lang}/{target.name}"

    snapshot = {
        "lang": lang,
        "credentials": {
            "user": {"login": "user", "password": "user"},
            "admin": {"login": "admin", "password": "admin"},
        },
        "routes": routes,
        "assets": media,
    }
    snapshot = rewrite_for_publication(snapshot, lang)
    save_snapshot(DEMO_DIR / "snapshot" / f"{lang}.json", snapshot)
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo.capture")
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--lang", choices=("ru", "en", "all"), default="all")
    parser.add_argument("--password", required=True, help="password of the demo accounts")
    parser.add_argument("--force", action="store_true", help="confirm writing into the live database")
    args = parser.parse_args(argv)

    languages = ("ru", "en") if args.lang == "all" else (args.lang,)
    for lang in languages:
        snapshot = capture_language(args.base_url, lang, password=args.password, force=args.force)
        print(f"{lang}: {len(snapshot['routes'])} routes, {len(snapshot['assets'])} assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
