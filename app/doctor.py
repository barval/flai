"""One readiness report for an installation.

`flask doctor` answers the question a support ticket starts with: which part of
the stack is not in the state this version expects. Every check is read-only and
tolerant: a probe that raises becomes a failed check with its message, never a
traceback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

OK = "ok"
WARN = "warn"
FAIL = "fail"

# Tables whose absence means the schema never finished initialising.
_REQUIRED_TABLES = (
    "chat_sessions",
    "messages",
    "model_configs",
    "rag_chunks",
    "rag_stats",
    "tasks",
    "task_steps",
    "settings",
)


@dataclass
class Check:
    id: str
    status: str
    title: str
    detail: str
    hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "status": self.status,
            "title": self.title,
            "detail": self.detail,
            "hint": self.hint,
        }


def _check_database(_app: Any) -> Check:
    from app.database import get_db

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_database() AS db")
        name = (cursor.fetchone() or {}).get("db", "?")
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        present = {row["table_name"] for row in cursor.fetchall()}
    missing = [table for table in _REQUIRED_TABLES if table not in present]
    if missing:
        return Check(
            "database",
            FAIL,
            "PostgreSQL",
            f"connected to {name}, missing tables: {', '.join(missing)}",
            "start the app once so init_db() creates the schema, or restore a backup",
        )
    return Check("database", OK, "PostgreSQL", f"connected to {name}, schema complete")


def _check_redis(app: Any) -> Check:
    redis = getattr(getattr(app, "request_queue", None), "redis", None)
    if redis is None:
        return Check("redis", WARN, "Redis", "the queue has no client yet", "restart the web container")
    redis.ping()
    return Check("redis", OK, "Redis", "ping answered")


def _check_llama_swap(_app: Any) -> Check:
    import requests

    from app.config import setting
    from app.model_status import model_is_ready

    url = str(setting("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")).rstrip("/")
    response = requests.get(f"{url}/running", timeout=5)
    response.raise_for_status()
    running = response.json().get("running", [])
    ready = [entry.get("model") for entry in running if entry.get("state") == "ready"]
    roles = [role for role in ("multimodal", "reasoning") if model_is_ready(role)]
    if roles:
        return Check("llama_swap", OK, "llama-swap", f"reachable, serving {', '.join(roles)}")
    if ready:
        return Check("llama_swap", WARN, "llama-swap", f"reachable, running {', '.join(map(str, ready))} but no chat role")
    return Check("llama_swap", WARN, "llama-swap", "reachable, nothing resident", "the first request will load a model")


def _check_models(_app: Any) -> Check:
    from app.model_config import get_model_config
    from app.universal_mode import is_enabled, mmproj_for, model_path_for

    # In single-model mode the universal row serves both roles: the pair's rows
    # are inactive, and their files may well be absent on purpose.
    modules = ["universal", "embedding"] if is_enabled() else ["universal", "multimodal", "reasoning", "embedding"]
    missing: list[str] = []
    checked: list[str] = []
    for module in modules:
        config = get_model_config(module, _raw=True) or {}
        name = str(config.get("model_name") or "").strip()
        if not name:
            continue
        checked.append(f"{module}={name}")
        # The name may be a file or the model's own directory; resolve it the
        # same way the llama-swap generator does.
        path = model_path_for(name) or name
        if not path or not os.path.exists(path):
            missing.append(f"{module}: {name} is not on disk")
            continue
        if module in ("multimodal", "universal") and mmproj_for(name) is None:
            missing.append(f"{module}: no vision projector for {name}")
    if not checked:
        return Check("models", FAIL, "Models", "no model is configured", "pick one in Admin → Models")
    if missing:
        return Check(
            "models",
            FAIL,
            "Models",
            "; ".join(missing),
            "download the model (Admin → Model Hub) or correct the name",
        )
    return Check("models", OK, "Models", f"{len(checked)} configured: {', '.join(checked)}")


def _check_vram(_app: Any) -> Check:
    from app.platform_detect import get_platform_info
    from app.resource_manager import get_resource_manager

    info = get_platform_info()
    if info.accelerator == "none" or not info.total_vram_mb:
        return Check("vram", OK, "Memory", f"no GPU ({info.platform}); models run on CPU")
    manager = get_resource_manager()
    needed = 0
    for role in ("multimodal", "reasoning"):
        try:
            needed = max(needed, manager.get_vram_needed_mb(role))
        except Exception:  # noqa: BLE001 — an unmeasurable model is reported by the models check
            continue
    detail = f"{info.total_vram_mb} MB on {info.gpu_name}, largest role needs {needed} MB"
    if needed > info.total_vram_mb:
        return Check(
            "vram",
            WARN,
            "Memory",
            detail,
            "the model will be partly offloaded; lower the context window or pick a smaller model",
        )
    return Check("vram", OK, "Memory", detail)


def _check_storage(app: Any) -> Check:
    problems = []
    for key in ("DOCUMENTS_FOLDER", "UPLOAD_FOLDER"):
        path = str(app.config.get(key) or "")
        if not path:
            problems.append(f"{key} is not set")
        elif not os.path.isdir(path):
            problems.append(f"{key} does not exist ({path})")
        elif not os.access(path, os.W_OK):
            problems.append(f"{key} is not writable ({path})")
    if problems:
        return Check("storage", FAIL, "Storage", "; ".join(problems), "check the mounted volumes and their owner")
    return Check("storage", OK, "Storage", "document and upload folders are writable")


def _check_search(app: Any) -> Check:
    module = (getattr(app, "modules", None) or {}).get("search")
    if module is None:
        return Check("search", WARN, "Web search", "the search module is not loaded")
    if module.check_availability():
        return Check("search", OK, "Web search", "SearXNG answers")
    return Check(
        "search",
        WARN,
        "Web search",
        "SearXNG is unreachable",
        "start the searxng profile, or rely on a per-user Tavily key",
    )


def _check_optional_services(app: Any) -> Check:
    modules = getattr(app, "modules", None) or {}
    states = []
    down = []
    for name, title in (
        ("rag", "Qdrant"),
        ("audio", "Whisper"),
        ("tts", "TTS"),
        ("crawl", "Crawler"),
        ("slm", "SLM"),
    ):
        module = modules.get(name)
        if module is None:
            continue
        try:
            available = bool(module.check_availability())
        except Exception:  # noqa: BLE001 — a probe failure is "down"
            available = False
        states.append(f"{title}: {'ok' if available else 'down'}")
        if not available:
            down.append(title)
    if not states:
        return Check("services", WARN, "Optional services", "no optional module is loaded")
    if down:
        return Check(
            "services",
            WARN,
            "Optional services",
            "; ".join(states),
            f"start the compose profile for {', '.join(down)} if you need it",
        )
    return Check("services", OK, "Optional services", "; ".join(states))


CHECKS = (
    _check_database,
    _check_redis,
    _check_llama_swap,
    _check_models,
    _check_vram,
    _check_storage,
    _check_search,
    _check_optional_services,
)


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if check.status == WARN]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": FAIL if self.failures else OK,
            "checks": [check.as_dict() for check in self.checks],
        }


def run_checks(app: Any) -> Report:
    """Run every check; a probe that raises becomes a failed check."""
    report = Report()
    for probe in CHECKS:
        try:
            report.checks.append(probe(app))
        except Exception as exc:  # noqa: BLE001 — a doctor must report, not raise
            report.checks.append(
                Check(probe.__name__.removeprefix("_check_"), FAIL, probe.__name__, str(exc)[:300])
            )
    return report


def render(report: Report) -> str:
    """One line per check, then the verdict."""
    marks = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}
    lines = [f"{marks[check.status]}  {check.title:<20} {check.detail}" for check in report.checks]
    for check in report.failures + report.warnings:
        if check.hint:
            lines.append(f"      → {check.title}: {check.hint}")
    verdict = f"{len(report.failures)} failed, {len(report.warnings)} warning(s)"
    lines.append(verdict if report.failures else f"ready — {verdict}")
    return "\n".join(lines)
