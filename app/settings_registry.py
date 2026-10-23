# app/settings_registry.py
"""Which environment variables the admin panel may override.

The registry is the single source of truth: the API builds its schema from it,
validation follows it, and a test fails when a new environment variable is
neither listed here nor declared bootstrap-only. Adding a parameter is one line.

Precedence is ``database > environment > built-in default``: an installation
that never touched the panel behaves exactly like before.
"""

from __future__ import annotations

from typing import Any

# Keys that must keep coming from .env: needed before the database is reachable,
# define process behaviour, or point at mounted volumes.
BOOTSTRAP_KEYS = {
    "SECRET_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "UPLOAD_FOLDER",
    "DOCUMENTS_FOLDER",
    "BRANDING_FOLDER",
    "HTTPS_ENABLED",
    "FLAI_PLATFORM",
    "DEBUG_JS",
    "DEBUG_TRANSLATIONS",
}

# Environment name → app.config key, when they differ.
ENV_TO_CONFIG = {"TIMEZONE": "TIMEZONE_STR"}

# Never returned to the browser; only a mask and a "replace" action.
SECRET_KEYS = {
    "QDRANT_API_KEY",
    "HUGGINGFACE_TOKEN",
    "CRAWL_API_TOKEN",
}

# Group id → (label, keys). The order here is the order in the panel.
GROUPS: dict[str, tuple[str, list[str]]] = {
    "search": (
        "Search and crawl",
        [
            "TAVILY_ENABLED",
            "TAVILY_API_URL",
            "TAVILY_TIMEOUT",
            "TAVILY_USAGE_TIMEOUT",
            "TAVILY_MAX_RESULTS",
            "TAVILY_SEARCH_DEPTH",
            "TAVILY_ADMIN_CACHE_TTL",
            "TAVILY_NEGATIVE_CACHE_TTL",
            "SEARXNG_URL",
            "SEARXNG_TIMEOUT",
            "SEARXNG_MAX_RESULTS",
            "SEARXNG_MAX_RESULTS_CHARS",
            "CRAWL_ENABLED",
            "CRAWLER_URL",
            "CRAWL_API_TOKEN",
            "CRAWL_MAX_PAGES",
            "CRAWL_MAX_DEPTH",
            "CRAWL_TIMEOUT_S",
            "CRAWL_PAGE_TIMEOUT_S",
            "CRAWL_MAX_PAGE_CHARS",
            "CRAWL_MAX_TOTAL_CHARS",
            "CRAWL_CONCURRENCY",
            "RAG_MAX_RESULTS_CHARS",
        ],
    ),
    "documents": (
        "Documents and RAG",
        [
            "QDRANT_URL",
            "QDRANT_API_KEY",
            "RAG_CHUNK_SIZE",
            "RAG_CHUNK_OVERLAP",
            "RAG_CHUNK_STRATEGY",
            "RAG_TOP_K",
            "RAG_CONTEXT_PERCENT",
            "OCR_TIME_BUDGET_S",
            "MAX_DOCUMENT_SIZE_MB",
            "MAX_DOCUMENTS_PER_USER",
            "MAX_DOCUMENTS_STORAGE_MB",
            "MAX_UPLOAD_STORAGE_MB",
            "MAX_EXTENSION_LENGTH",
            "MAX_CONTENT_LENGTH_MB",
        ],
    ),
    "tasks": (
        "Tasks",
        [
            "TASKS_ENABLED",
            "TASK_TOTAL_BUDGET_S",
            "TASK_MAX_STEPS",
            "TASK_MAX_REVISIONS",
            "TASK_REPLAN_EVERY_N",
            "TASK_STEP_MAX_ATTEMPTS",
            "TASK_STEP_MAX_REPAIRS",
            "TASK_STEP_REDO_MAX",
            "TASK_QUIET_PERIOD_S",
            "TASK_POLL_INTERVAL_S",
            "TASK_SUBTASK_POLL_CYCLES",
            "TASK_SUBTASK_ALIVE_UNKNOWN_MAX",
            "TASK_CONTINUITY_ENABLED",
            "TASK_CONTINUITY_MAX_CHARS",
            "TASK_VRAM_WAIT_S",
        ],
    ),
    "models": (
        "Models and context",
        [
            "LLAMACP_BACKEND",
            "LLAMA_SWAP_URL",
            "LLAMACPP_URL",
            "CONTEXT_HISTORY_PERCENT",
            "CONTEXT_SAFETY_MARGIN",
            "TEMPLATE_OVERHEAD_TOKENS",
            "TOKEN_CHARS",
            "IMAGE_TOKENS_PER_IMAGE",
            "MEDIA_CONTEXT_CHARS",
            "ROUTER_CONTEXT_MESSAGES",
            "ROUTER_CONTEXT_MSG_CHARS",
            "ROUTER_SLM_FACTS",
            "SERVICE_RETRY_ATTEMPTS",
            "SERVICE_RETRY_DELAY",
            "VRAM_WAIT_TIMEOUT_S",
        ],
    ),
    "media": (
        "Media generation",
        [
            "SD_WRAPPER_URL",
            "SD_MODEL_TYPE",
            "SD_EDIT_MODEL_TYPE",
            "SD_CPP_TIMEOUT",
            "SD_CPP_DEFAULT_WIDTH",
            "SD_CPP_DEFAULT_HEIGHT",
            "SD_CPP_DEFAULT_STEPS",
            "SD_CPP_DEFAULT_CFG_SCALE",
            "LTX_VIDEO_WRAPPER_URL",
            "LTX_VIDEO_MODEL",
            "LTX_VIDEO_TIMEOUT",
            "MAX_IMAGE_SIZE",
            "MAX_IMAGE_SIZE_MB",
            "MAX_IMAGE_SIZE_MULTI",
            "MAX_IMAGE_WIDTH",
            "MAX_IMAGE_HEIGHT",
            "MAX_CHAT_IMAGES",
            "MAX_CHAT_AUDIO",
            "MAX_AUDIO_SIZE_MB",
            "MAX_VOICE_SIZE_MB",
        ],
    ),
    "rlm": (
        "Deep analysis",
        [
            "RLM_ENABLED",
            "RLM_ACTOR_MODEL",
            "RLM_MAX_STEPS",
            "RLM_TASK_TIMEOUT",
            "RLM_CODE_TIMEOUT",
            "RLM_OBS_TRUNC",
            "RLM_SUB_MAX_TOKENS",
            "RLM_WEB_MAX_FETCHES",
            "RLM_MAX_CORPUS_CHARS",
        ],
    ),
    "voice": (
        "Voice",
        [
            "PIPER_URL",
            "PIPER_TIMEOUT",
            "KOKORO_URL",
            "KOKORO_TIMEOUT",
            "WHISPER_API_URL",
            "WHISPER_API_TIMEOUT",
        ],
    ),
    "memory": (
        "Memory and history",
        [
            "SLM_URL",
            "SLM_RECALL_LIMIT",
            "SLM_SIMILARITY_THRESHOLD",
            "SLM_MIN_CONFIDENCE_FOR_DECAY",
            "SLM_TEMPORAL_DECAY_DAYS",
            "MERGE_CONTEXT_SIZE",
            "MERGE_FACT_MAX_CHARS",
            "MERGE_MAX_FACTS",
            "MERGE_MAX_FIT_FACTS",
            "SESSION_SUMMARY_MAX_CHARS",
            "SESSION_SUMMARY_MAX_FETCH",
            "SESSION_SUMMARY_MIN_MESSAGES",
            "HISTORY_SEARCH_LIMIT",
            "HISTORY_MAX_RESULTS_CHARS",
            "HISTORY_MAX_MESSAGE_CHARS",
            "MAX_HISTORY_MESSAGES",
            "MESSAGES_DEFAULT_LIMIT",
            "MESSAGES_MAX_LIMIT",
        ],
    ),
    "api": (
        "API",
        [
            "API_ENABLED",
            "API_RATE_LIMIT",
            "API_MAX_CONCURRENT_WAITS",
            "API_SYNC_MAX_WAIT",
            "API_CORS_ORIGINS",
            "REDIS_RESULT_TTL",
        ],
    ),
    "cameras": (
        "Cameras",
        [
            "CAMERA_ENABLED",
            "CAMERA_API_URL",
            "CAMERA_API_TIMEOUT",
            "CAMERA_CHECK_INTERVAL",
        ],
    ),
    "service": (
        "Service",
        [
            "TIMEZONE",
            "HUGGINGFACE_TOKEN",
            "MODEL_HUB_MAX_FILE_GB",
            "MODEL_HUB_FREE_MARGIN_GB",
            "MODEL_HUB_SEARCH_LIMIT",
            "MODEL_HUB_TIMEOUT_S",
            "QUEUE_MAX_WAIT_TIME",
        ],
    ),
    "debug": (
        "Debug",
        [
            "DEBUG_API_ENABLED",
            "DEBUG_TOKEN_ESTIMATION",
        ],
    ),
}


def config_key(key: str) -> str:
    """The app.config key that holds this setting."""
    return ENV_TO_CONFIG.get(key, key)


def movable_keys() -> list[str]:
    """Every key the panel may override, in display order."""
    keys: list[str] = []
    for _group, (_label, group_keys) in GROUPS.items():
        keys.extend(group_keys)
    return keys


def _kind_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def build_schema(app) -> list[dict]:
    """Grouped schema for the panel: label, key, type, current value, default."""
    groups: list[dict] = []
    for group_id, (label, keys) in GROUPS.items():
        fields = []
        for key in keys:
            value = app.config.get(config_key(key))
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                value = str(value)
            fields.append(
                {
                    "key": key,
                    "kind": _kind_of(value),
                    "secret": key in SECRET_KEYS,
                    "value": None if key in SECRET_KEYS else value,
                    "has_value": bool(value) if key in SECRET_KEYS else True,
                    "default": None if key in SECRET_KEYS else _default_of(key, app),
                }
            )
        groups.append({"id": group_id, "label": label, "fields": fields})
    return groups


_DEFAULTS: dict[str, Any] = {}


def remember_defaults(app) -> None:
    """Capture the .env/default values once, before any override is applied."""
    for key in movable_keys():
        _DEFAULTS[key] = app.config.get(config_key(key))


def _default_of(key: str, app) -> Any:
    return _DEFAULTS.get(key, app.config.get(key))


def default_for(key: str) -> Any:
    """The .env/built-in value captured at startup (used by the reset action)."""
    return _DEFAULTS.get(key)


def coerce(key: str, raw: Any, current_kind: str) -> tuple[Any, str | None]:
    """Validate and convert one submitted value; returns (value, error message)."""
    if key not in movable_keys():
        return None, "Unknown setting"
    if current_kind == "bool":
        if isinstance(raw, bool):
            return raw, None
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True, None
        if text in ("0", "false", "no", "off"):
            return False, None
        return None, "Expected true or false"
    if current_kind == "int":
        try:
            return int(str(raw).strip()), None
        except (TypeError, ValueError):
            return None, "Expected a whole number"
    if current_kind == "float":
        try:
            return float(str(raw).strip().replace(",", ".")), None
        except (TypeError, ValueError):
            return None, "Expected a number"
    return str(raw), None
