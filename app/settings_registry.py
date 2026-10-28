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
            "RAG_HYBRID_ENABLED",
            "RAG_HYBRID_CANDIDATES",
            "RAG_RRF_K",
            "RAG_RERANK_ENABLED",
            "RAG_RERANK_CANDIDATES",
            "RAG_RERANK_TIMEOUT",
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
            "VIDEO_BACKEND",
            "LTX_VIDEO_WRAPPER_URL",
            "LTX_VIDEO_MODEL",
            "LTX_VIDEO_TIMEOUT",
            "WAN_VIDEO_URL",
            "WAN_VIDEO_MODEL",
            "WAN_VIDEO_TIMEOUT",
            "WAN_VIDEO_STEPS",
            "WAN_VIDEO_CFG_SCALE",
            "WAN_VIDEO_FLOW_SHIFT",
            "WAN_VIDEO_ALLOW_CPU",
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
            "WHISPER_GPU",
            "WHISPER_VRAM_MB",
        ],
    ),
    "memory": (
        "Memory and history",
        [
            "SLM_URL",
            "SLM_RECALL_LIMIT",
            "SLM_SIMILARITY_THRESHOLD",
            "SLM_MIN_CONFIDENCE_FOR_DECAY",
            "SLM_AUTO_EXTRACT",
            "SLM_MIN_RECALL_SCORE",
            "MEMORY_EXTRACT_QUIET_S",
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
                    "description": DESCRIPTIONS.get(key, ""),
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


DESCRIPTIONS: dict[str, str] = {
    "TAVILY_ENABLED": "Use the per-user Tavily key before the local search engine",
    "TAVILY_API_URL": "Tavily API base URL",
    "TAVILY_TIMEOUT": "Tavily search request timeout (seconds)",
    "TAVILY_USAGE_TIMEOUT": "Timeout for the Tavily quota lookup (seconds)",
    "TAVILY_MAX_RESULTS": "Maximum results requested from Tavily",
    "TAVILY_SEARCH_DEPTH": "Tavily depth: basic, advanced, fast or ultra-fast",
    "TAVILY_ADMIN_CACHE_TTL": "How long an answered quota lookup is reused (seconds)",
    "TAVILY_NEGATIVE_CACHE_TTL": "How long an unreachable quota endpoint is remembered (seconds)",
    "SEARXNG_URL": "Local SearXNG base URL",
    "SEARXNG_TIMEOUT": "SearXNG request timeout (seconds)",
    "SEARXNG_MAX_RESULTS": "Maximum results requested from SearXNG",
    "SEARXNG_MAX_RESULTS_CHARS": "Characters of search context passed to the model",
    "CRAWL_ENABLED": "Enable the Crawl4AI page reader and site crawler",
    "CRAWLER_URL": "Crawl4AI service URL",
    "CRAWL_API_TOKEN": "Bearer token for the Crawl4AI service",
    "CRAWL_MAX_PAGES": "Maximum pages per crawl",
    "CRAWL_MAX_DEPTH": "Maximum link depth per crawl",
    "CRAWL_TIMEOUT_S": "Whole-crawl wall-clock limit (seconds)",
    "CRAWL_PAGE_TIMEOUT_S": "Single page fetch timeout (seconds)",
    "CRAWL_MAX_PAGE_CHARS": "Characters kept per crawled page",
    "CRAWL_MAX_TOTAL_CHARS": "Characters kept across the whole crawl",
    "CRAWL_CONCURRENCY": "Parallel crawl requests",
    "RAG_MAX_RESULTS_CHARS": "Characters of document context passed to the model",
    "QDRANT_URL": "Qdrant vector database URL",
    "QDRANT_API_KEY": "Qdrant API key (optional)",
    "RAG_CHUNK_SIZE": "Characters per indexed chunk",
    "RAG_CHUNK_OVERLAP": "Characters shared by neighbouring chunks",
    "RAG_CHUNK_STRATEGY": "Chunking strategy: fixed or recursive",
    "RAG_HYBRID_ENABLED": "Merge dense vector and full-text retrieval with reciprocal rank fusion",
    "RAG_HYBRID_CANDIDATES": "Candidates each retriever returns before fusion",
    "RAG_RRF_K": "Reciprocal rank fusion constant",
    "RAG_RERANK_ENABLED": "Reorder the top candidates with the CPU cross-encoder",
    "RAG_RERANK_CANDIDATES": "How many top candidates the reranker reorders",
    "RAG_RERANK_TIMEOUT": "Reranker request timeout (seconds)",
    "RAG_TOP_K": "Chunks retrieved per document query",
    "RAG_CONTEXT_PERCENT": "Share of the context window given to documents",
    "OCR_TIME_BUDGET_S": "Time budget for scanned-PDF OCR (seconds, 0 = unlimited)",
    "MAX_DOCUMENT_SIZE_MB": "Largest document a user may upload (MB)",
    "MAX_DOCUMENTS_PER_USER": "Documents per user, also the folder limit",
    "MAX_DOCUMENTS_STORAGE_MB": "Total document storage per user (MB)",
    "MAX_UPLOAD_STORAGE_MB": "Default upload limit per user (MB), overridable in Users",
    "MAX_EXTENSION_LENGTH": "Longest accepted file extension",
    "MAX_CONTENT_LENGTH_MB": "Largest accepted HTTP request body (MB)",
    "TASKS_ENABLED": "Enable background tasks",
    "TASK_TOTAL_BUDGET_S": "Pure execution budget per task (seconds)",
    "TASK_MAX_STEPS": "Hard ceiling on the plan length",
    "TASK_MAX_REVISIONS": "Accepted plan rewrites per task",
    "TASK_REPLAN_EVERY_N": "Passing media steps between plan reviews",
    "TASK_STEP_MAX_ATTEMPTS": "Attempts per step, verification included",
    "TASK_STEP_MAX_REPAIRS": "Local edits after each generation attempt",
    "TASK_STEP_REDO_MAX": "Redo passes for a rejected step",
    "TASK_QUIET_PERIOD_S": "Chat-free window before a step may run (seconds)",
    "TASK_POLL_INTERVAL_S": "Executor cycle interval (seconds)",
    "TASK_SUBTASK_POLL_CYCLES": "Executor cycles between media subtask checks",
    "TASK_SUBTASK_ALIVE_UNKNOWN_MAX": "How long an unknown subtask state is tolerated",
    "TASK_CONTINUITY_ENABLED": "Build a visual continuity sheet for image series",
    "TASK_CONTINUITY_MAX_CHARS": "Characters kept in the continuity sheet",
    "TASK_VRAM_WAIT_S": "VRAM wait before a task step gives up (seconds)",
    "LLAMACP_BACKEND": "LLM backend: llama-swap or direct llama.cpp",
    "LLAMA_SWAP_URL": "llama-swap API URL",
    "LLAMACPP_URL": "Direct llama.cpp API URL",
    "CONTEXT_HISTORY_PERCENT": "Share of the context window given to history",
    "CONTEXT_SAFETY_MARGIN": "Tokens reserved below the context limit",
    "TEMPLATE_OVERHEAD_TOKENS": "Tokens reserved for the prompt template",
    "TOKEN_CHARS": "Characters per token used for estimates",
    "IMAGE_TOKENS_PER_IMAGE": "Tokens charged for one attached image",
    "MEDIA_CONTEXT_CHARS": "Characters of media descriptions kept in context",
    "ROUTER_CONTEXT_MESSAGES": "Recent messages passed to the router",
    "ROUTER_CONTEXT_MSG_CHARS": "Characters per message passed to the router",
    "ROUTER_SLM_FACTS": "Long-term facts passed to the router",
    "SERVICE_RETRY_ATTEMPTS": "Startup health-check attempts per service",
    "SERVICE_RETRY_DELAY": "Delay between startup health checks (seconds)",
    "VRAM_WAIT_TIMEOUT_S": "How long to wait for VRAM to free (seconds)",
    "SD_WRAPPER_URL": "Image generation wrapper URL",
    "SD_MODEL_TYPE": "Model used for image generation",
    "SD_EDIT_MODEL_TYPE": "Model used for image editing",
    "SD_CPP_TIMEOUT": "Image generation timeout (seconds)",
    "SD_CPP_DEFAULT_WIDTH": "Default image width (pixels)",
    "SD_CPP_DEFAULT_HEIGHT": "Default image height (pixels)",
    "SD_CPP_DEFAULT_STEPS": "Default diffusion steps",
    "SD_CPP_DEFAULT_CFG_SCALE": "Default classifier-free guidance scale",
    "VIDEO_BACKEND": "Video backend (ltx or wan)",
    "LTX_VIDEO_WRAPPER_URL": "LTX-Video wrapper URL",
    "LTX_VIDEO_MODEL": "LTX-Video model file",
    "LTX_VIDEO_TIMEOUT": "LTX-Video generation timeout (seconds)",
    "WAN_VIDEO_URL": "Wan video wrapper URL (sd.cpp container)",
    "WAN_VIDEO_MODEL": "Wan 2.2 model file",
    "WAN_VIDEO_TIMEOUT": "Wan generation timeout (seconds)",
    "WAN_VIDEO_STEPS": "Wan denoising steps (4 for Turbo)",
    "WAN_VIDEO_CFG_SCALE": "Wan classifier-free guidance scale",
    "WAN_VIDEO_FLOW_SHIFT": "Wan flow shift",
    "WAN_VIDEO_ALLOW_CPU": "Allow Wan video generation on a CPU-only host",
    "MAX_IMAGE_SIZE": "Largest single image side (pixels)",
    "MAX_IMAGE_SIZE_MB": "Largest image upload (MB)",
    "MAX_IMAGE_SIZE_MULTI": "Side limit when several images are attached",
    "MAX_IMAGE_WIDTH": "Maximum accepted image width",
    "MAX_IMAGE_HEIGHT": "Maximum accepted image height",
    "MAX_CHAT_IMAGES": "Images per chat message",
    "MAX_CHAT_AUDIO": "Audio attachments per chat message",
    "MAX_AUDIO_SIZE_MB": "Largest audio upload (MB)",
    "MAX_VOICE_SIZE_MB": "Largest voice message (MB)",
    "RLM_ENABLED": "Enable deep analysis",
    "RLM_ACTOR_MODEL": "Model driving the analysis loop",
    "RLM_MAX_STEPS": "Hard ceiling on analysis steps",
    "RLM_TASK_TIMEOUT": "Wall-clock limit (0 = derived from the step budget)",
    "RLM_CODE_TIMEOUT": "Sandbox execution limit (seconds)",
    "RLM_OBS_TRUNC": "Characters kept per observation",
    "RLM_SUB_MAX_TOKENS": "Tokens per sub-model call",
    "RLM_WEB_MAX_FETCHES": "Web lookups per analysis",
    "RLM_MAX_CORPUS_CHARS": "Largest corpus accepted (characters)",
    "PIPER_URL": "Piper TTS service URL",
    "PIPER_TIMEOUT": "Piper synthesis timeout (seconds)",
    "KOKORO_URL": "Kokoro TTS service URL",
    "KOKORO_TIMEOUT": "Kokoro synthesis timeout (seconds)",
    "WHISPER_API_URL": "Whisper speech-recognition URL",
    "WHISPER_GPU": "Run Whisper transcription on the GPU",
    "WHISPER_VRAM_MB": "VRAM budget for the Whisper container (MB)",
    "WHISPER_API_TIMEOUT": "Transcription timeout (seconds)",
    "SLM_URL": "SuperLocalMemory service URL",
    "SLM_RECALL_LIMIT": "Facts recalled per message",
    "SLM_SIMILARITY_THRESHOLD": "Minimum similarity for a recalled fact",
    "SLM_AUTO_EXTRACT": "Automatic fact extraction mode (auto, on, off)",
    "SLM_MIN_RECALL_SCORE": "Minimum recall score for non-profile facts",
    "MEMORY_EXTRACT_QUIET_S": "Quiet window before fact extraction starts (seconds)",
    "SLM_MIN_CONFIDENCE_FOR_DECAY": "Confidence below which a fact starts decaying",
    "SLM_TEMPORAL_DECAY_DAYS": "Days after which a fact loses weight",
    "MERGE_CONTEXT_SIZE": "Facts considered in one merge pass",
    "MERGE_FACT_MAX_CHARS": "Characters per merged fact",
    "MERGE_MAX_FACTS": "Facts kept per merge batch",
    "MERGE_MAX_FIT_FACTS": "Facts that must fit the model window",
    "SESSION_SUMMARY_MAX_CHARS": "Characters in a rolling session summary",
    "SESSION_SUMMARY_MAX_FETCH": "Messages read when building a summary",
    "SESSION_SUMMARY_MIN_MESSAGES": "Trimmed messages needed before summarizing",
    "HISTORY_SEARCH_LIMIT": "History matches per search",
    "HISTORY_MAX_RESULTS_CHARS": "Characters of history context passed to the model",
    "HISTORY_MAX_MESSAGE_CHARS": "Characters per history message",
    "MAX_HISTORY_MESSAGES": "Messages kept in the conversation window",
    "MESSAGES_DEFAULT_LIMIT": "Messages loaded per session by default",
    "MESSAGES_MAX_LIMIT": "Largest message page the API returns",
    "API_ENABLED": "Enable the public OpenAI-compatible API",
    "API_RATE_LIMIT": "Rate limits, e.g. 60 per minute;1000 per hour",
    "API_MAX_CONCURRENT_WAITS": "Synchronous API waiters per instance",
    "API_SYNC_MAX_WAIT": "How long a synchronous request waits (seconds)",
    "API_CORS_ORIGINS": "Allowed origins for /v1, comma separated",
    "REDIS_RESULT_TTL": "How long API results stay in Redis (seconds)",
    "CAMERA_ENABLED": "Enable camera support",
    "CAMERA_API_URL": "Room snapshot service URL",
    "CAMERA_API_TIMEOUT": "Camera service timeout (seconds)",
    "CAMERA_CHECK_INTERVAL": "Camera availability check interval (seconds)",
    "TIMEZONE": "IANA timezone used for dates and monitoring",
    "HUGGINGFACE_TOKEN": "Token for gated Hugging Face repositories",
    "MODEL_HUB_MAX_FILE_GB": "Largest model file the Hub may download (GB)",
    "MODEL_HUB_FREE_MARGIN_GB": "Free disk margin kept after a download (GB)",
    "MODEL_HUB_SEARCH_LIMIT": "Results per Hugging Face search",
    "MODEL_HUB_TIMEOUT_S": "Download timeout (seconds)",
    "QUEUE_MAX_WAIT_TIME": "Largest queue wait before a task is rejected (seconds)",
    "DEBUG_API_ENABLED": "Expose the debug endpoints",
    "DEBUG_TOKEN_ESTIMATION": "Log token-estimation details",
}
