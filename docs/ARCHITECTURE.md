# Architecture — FLAI v9.0

This document describes the internal architecture of FLAI in detail. Read it when modifying core logic, queue, modules, or data flow.

For critical rules and commands, see the root `AGENTS.md`.

## Entrypoint & Structure

- **`app/__init__.py:create_app()`** — Flask application factory.
- **Blueprints** (`app/routes/`): `auth`, `chat`, `admin`, `queue`, `tts`, `messages`, `sessions`, `documents`, `backups`, `events`, `debug`.
- **Modules** (`modules/`): `base/router`, `multimodal`, `sd_cpp`, `cam`, `rag`, `audio`, `tts`, `slm`, `search`, `video`.
- **Background tasks** (`app/tasks/`): `dry_load.py` (model dry-load after admin save), `health_monitor.py` (crash-loop watchdog).
- **Templates** (`app/templates/`): `admin.html`, `base.html`, `chat.html`, `login.html`.
- **Static**: `app/static/css/` (all CSS), `app/static/js/` (all JS). No inline styles, no CDN.

## LLM Client

`app/llamacpp_client.py:LlamaCppClient` with two backends:

- **`DirectLlamaBackend`** — direct HTTP calls to `llama-server` at `LLAMACPP_URL`.
- **`LlamaSwapBackend`** — calls via `llama-swap` proxy at `LLAMA_SWAP_URL`.

Selected by `LLAMACP_BACKEND` env var (default: `llama-swap`).

`_ensure_vram()` in `llamacpp_client.py` returns `bool` — every chat/stream call checks VRAM before POST. No model will ever receive 502 due to VRAM — insufficient VRAM returns a proper error message.

## Queue System

`app/queue.py:RedisRequestQueue` — two workers with strict GPU serialization.

### Fast Worker (mostly CPU; embedding is GPU-light)
- Router (chat model)
- Text processing
- Audio (TTS/STT)
- **RAG search only** (embedding + Qdrant, ~500 MB)

The chat model (~2 GiB) stays hot in VRAM permanently.

### Slow Worker (GPU-heavy)
- Multimodal (Qwen3VL-8B)
- SD (Stable Diffusion)
- LTX-Video
- **Reasoning** (16GB+: gpt-oss-20b MXFP4 on Blackwell / Q4_K_M on other, 12/8GB: gemma-4-E4B)
- **RAG generation** (via reasoning model)

Strictly sequential — only one GPU task runs at a time.

### Background Tasks
`fact_extraction_task` and `fact_merge_task` are background tasks that run on the slow worker after chat responses. They are defined in `_BACKGROUND_TASK_TYPES` and have special handling:
- **Not counted in queue status** — `get_user_requests_status()` excludes them from `processing` and `queued` display.
- **Not counted in user queue counter** — `_process_single_task()` skips `_decrement_user_queue_count()` for them.
- **Errors are silently logged** — both `_process_fact_extraction()` and `_process_fact_merge()` are wrapped in try/except so failures never leak to users via SSE.
- They are added directly to `slow_queue_key` via `redis.rpush()` without going through `add_request()`.

### Task Signing
Tasks are HMAC-signed JSON to prevent tampering.

## Database

PostgreSQL only via `app/database.py:get_db()` context manager (psycopg2 RealDictCursor). `DATABASE_URL` required.

**Tables**: `user_sessions`, `chat_sessions`, `messages`, `documents`, `session_visits`, `model_configs`, `user_storage`, `slm_import_progress`, `gguf_models_cache`, `camera_rooms`, `model_vram_estimates`.

## Docker & Services

**Mounts**:
- `./data/` → `/app/data`
- `./services/llamacpp/models/` → `/models:ro`
- `/var/run/docker.sock` for GPU detection and container restarts

**Additional services**:
- `services/room-snapshot-api/` — camera snapshots
- `services/qdrant/` — vector DB for RAG
- `services/openai-whisper/` — speech-to-text
- `services/piper/` — text-to-speech
- `services/superlocalmemory/` — long-term memory (SLM)
- `services/llamacpp/` — llama.cpp servers
- `llama-swap` — Docker image `ghcr.io/mostlygeek/llama-swap:cuda` (no local service directory)

**Docker compose profiles**: `with-image-gen`, `with-voice`, `with-rag`, `with-video`, `with-slm`, `with-search`.

## Model Lifecycle on a Single Consumer GPU

All llama.cpp models share a single `llm_fast` group with `swap: true` in llama-swap. At most ONE model is loaded in VRAM at any time.

**TTLs**:
- `chat` = 0 (never unload — stays hot permanently, only swapped when another model needs VRAM)
- `multimodal`, `reasoning`, `embedding` = 1s (unload 1 second after response)

Chat model preloaded at startup via `hooks.on_startup.preload: ["chat"]`. After every non-chat task, chat model is reloaded in a background thread (`_preload_chat_model_background()`) to eliminate cold starts.

SD and LTX-Video use separate GPU contexts.

### Sequence of Models

1. **Chat (Qwen3-4B-Instruct-2507, ~2 GiB)** — preloaded at startup, TTL=0. Default model for router and direct responses. MXFP4 variant (~2.0 GB) on Blackwell GPUs (native FP4), Q4_0 variant (~2.4 GB) on other architectures. Swapped out on demand. After other model finishes (TTL=1s → unloaded), `_preload_chat_model_background()` reloads chat via tiny completion request in a daemon thread.

2. **Multimodal (Qwen3VL-8B, 5 GiB)** — loaded on demand (camera, image analysis, video param gen). TTL=1s → **unloaded 1 second** after the response is sent. Context length 16384 to accommodate vision token counts from dynamic image tiling.

3. **Reasoning (16GB+: gpt-oss-20b, 12/8GB: gemma-4-E4B Q4_0)** — loaded on demand for complex queries. The 16GB+ tier uses `gpt-oss-20b-mxfp4` on Blackwell GPUs (native FP4) or `gpt-oss-20b-Q4_K_M` on other architectures. TTL=1s → unloaded 1 second after response.

4. **Embedding (bge-m3 Q8_0, 0.5 GiB)** — TTL=1s → unloaded 1 second after use.

5. **SD / LTX-Video** — before generation, llama-swap is asked to unload all models via `POST /api/models/unload` (implemented in `resource_manager.py:unload_llamacpp_model()`). This frees ~3-4 GiB VRAM (chat). LTX-Video container is **always** restarted after video generation (`_force_restart_ltx_video()`, no rate-limiting) to free CUDA context (~3 GB). Chat model is then reloaded via `_preload_chat_model_background()`.

### Example: Video Generation Request
router (chat) → [-VIDEO-] → multimodal loads (chat swapped out)
→ multimodal generates video params → multimodal unloads (TTL=1s)
→ video pipeline loads (full VRAM available) → video generated
→ container restart (CUDA context freed)
→ _preload_chat_model_background() → chat reloaded
→ next user request is instant


## SLM (SuperLocalMemory)

Per-user SQLite databases at `/app/data/slm/{user}/.superlocalmemory/memory.db`.

- **Daemon mode** (`slm serve start`) keeps embedding model in memory permanently.
- `services/superlocalmemory/slm_http.py` proxies requests to daemon at `localhost:8765` (no subprocess per call).
- **Per-user isolation**: recall reads directly from the user's private SQLite table (`atomic_facts`), not from the daemon's shared database.
- **Chat model** uses fast direct SQLite read (`ORDER BY created_at DESC`).
- **Reasoning model** uses full semantic search via subprocess `slm recall` (falls back to direct SQLite if no embeddings).
- **Remember** saves to both daemon (shared) and per-user DB (async subprocess).
- **Camera router parser**: uses text after `[-CAMERA-]` marker (room code), NOT `original_query` — preserves compatibility with Russian declensions.
- **Router retry on JSON error** — `process_message()` retries once if the router returns a garbled `{"error": ...}` response.
- SLM facts are injected into prompt context for BOTH chat and reasoning models.
- **SLM lazy availability re-check** — `_get_context_for_model()` always calls `slm.get_context()` (no `slm.available` check).
- **SLM dedup** — `_recall_from_user_db()` deduplicates facts by content (score `limit × 3`, returns unique). Configurable via `SLM_RECALL_LIMIT` (default 7).
- **Fact extraction** — background thread (`_extract_facts_bg()`) runs CPU-only after chat responses >20 chars. Extracts facts from the **user's query** (not the model's response) using rule-based pattern matching (`app/slm_rules.py`) — no LLM, no GPU lock. Model self-referential responses ("Как я могу помочь?", "Вот ваш ответ") and hallucinated news are filtered out by `_MODEL_RESPONSE_PATTERNS` and `_MODEL_CONTENT_PATTERNS`. Semantic deduplication via `/similarity` endpoint before saving. Wrapped in try/except — failures are logged but never surface to users.
- **Fact merge** — `_process_fact_merge()` runs on background queue during sleep mode. Uses rule-based pipeline: fast_cleanup → edit_distance_merge → fragment_merge → semantic_merge (via `/similarity`) → temporal_decay. No LLM, CPU-only.
- **SLM similarity** — `/similarity` endpoint in `slm_http.py` checks candidate text against existing facts using the daemon's embedding model. Returns cosine similarity score (0.0–1.0). Used by both extraction and merge for deduplication.
- **Temporal decay** — facts older than `SLM_TEMPORAL_DECAY_DAYS` (default 90) with confidence < `SLM_MIN_CONFIDENCE_FOR_DECAY` (default 0.5) are auto-archived during merge.
- **Memories cleanup** — Daemon writes to both `memories` and `atomic_facts` tables, but only `atomic_facts` is read by the system. `_cleanup_memories_for_user()` removes orphaned `memories` rows (no active `atomic_facts`). `_periodic_cleanup()` runs hourly as a daemon thread. `/cleanup-memories` POST endpoint for manual cleanup.
- **Skills list** — `prompts/{ru,en}/skills.txt` is the single source of truth for all capabilities. `format_prompt()` auto-injects `{skills_section}` when the template contains the placeholder.
- Background import on startup via `slm_import_progress` checkpoint table.
- Auto-cleaned on last session deletion (`_cleanup_slm_if_empty()` in `db.py`).
- Per-user SLM files are owned by `appuser (UID 1000)` — `start.sh` runs `chown -R appuser:appuser` on the shared volume.

## Response Style System

`STYLE_INSTRUCTIONS` in `modules/base.py` defines 5 styles: `neutral`, `academic`, `professional`, `friendly`, `funny`. Each includes explicit prohibitions for small model adherence.

- **Single source of truth** in `base.py`. Imported by `rag.py` and `multimodal.py` (no duplicates).
- Style injected into prompts via `{response_style}` placeholder in templates.
- User selects style via dropdown → saved to `session["response_style"]` → passed through queue → injected into system prompt.
- Temperature 0.7 (DB-configured) for all chat/reasoning models — enables style-sensitive generation.
- Router classification always uses `temperature=0.1` (hardcoded in `process_message()`) for deterministic query routing.

## Context Budget Calculation

`_get_context_for_model()` in `modules/base.py` manages token budget:

1. Fetch SLM facts first (two-phase: session-specific, then general)
2. Measure actual SLM token cost
3. Calculate history budget: `available_tokens - query_tokens - TEMPLATE_OVERHEAD - slm_tokens`
4. Load conversation history with SQL-level limit
5. Combine: history + SLM facts

- **No hardcoded reserves** — actual fact sizes used throughout.
- Safety margin: 85% of context window (`SAFETY_MARGIN=0.85`).
- Template overhead: 800 tokens (`TEMPLATE_OVERHEAD=800`).
- Final validation: `_validate_prompt_size()` enforces 95% hard limit.

## Tool Calling System

`app/tools.py` — native OpenAI-compatible tool calling with `--jinja` in llama-server.

**6 tools**:
1. `get_current_time`
2. `calculator` (safe AST eval)
3. `web_search` (SearXNG)
4. `rag_search` (Qdrant)
5. `camera_snapshot`
6. `time_calc` (9 date/time operations via Pendulum)

- `MAX_TOOL_ITERATIONS = 5`.
- Tools passed to `chat()`/`chat_stream()` via `tools` parameter.
- Streaming tool_call accumulation in `_tool_calls_by_index` dict.
- `execute_tool()` dispatches to executor functions.
- Tool definitions in `TOOL_DEFINITIONS` (OpenAI function-calling format).

### SSE Tool Events

`tool_call` and `tool_result` SSE events in `events.js`. `TOOL_LABELS` maps tool names to UI labels. `onToolCall(data)` shows progress label, `onToolResult(data)` removes it.

## Web Search Module

`modules/search.py:SearchModule` wraps SearXNG JSON API.

- `search(query, lang, max_results)` → list of dicts.
- `format_results_context()` → formatted string for reasoning model.
- Health check via `/healthz`.
- Docker profile `with-search`.
- Config: `SEARXNG_URL`, `SEARXNG_TIMEOUT`, `SEARXNG_MAX_RESULTS`.
- Router category 7 (`[-SEARCH-]`) in `prompts/{en,ru}/base_text.template`.

## Streaming Reasoning

`modules/base.py:generate_reasoning_response_stream()` yields tokens one-by-one via `_stream_chat()`.

`app/queue.py:_process_reasoning_request()` uses `generate_reasoning_response_stream()` instead of `process_reasoning()`. Tokens are published via `_publish_stream_token()`.

- **Server-side** `_strip_thinking_tags()` in `app/queue.py` handles three patterns: (1) `<think>...</think>` blocks are removed entirely; (2) `<|channel|>analysis<|message|>...<|end|>?` (reasoning) is stripped entirely — `<|end|>` is optional to avoid deleting the entire response when gpt-oss-20b omits it; (3) `<|channel|>commentary<|message|>...<|end|>` (actual answer) is **unwrapped** — tags removed, inner content kept. Malformed `<|channel|>...` without `<|message|>` is stripped. **`app/llamacpp_client.py:_process_stream_chunk()`** performs an unconditional buffer flush on `_thinking_active` transition to prevent token loss between `analysis` and `commentary` blocks. `_strip_generic_reasoning()` removes plain-text chain-of-thought (e.g. "Analyze Persona:", "Final Answer Generation:"). Server-side strip is now sufficient — `_strip_generic_reasoning()` was removed from `queue.py` (client-side filtering in `events.js` handles display).
- **Client-side** `_stripThinkingTags()` in `events.js` mirrors server logic (though currently unused — server-side filtering is sufficient). `_stripGenericReasoning()` strips generic reasoning patterns in real-time during streaming.

**Anti-hallucination for web search**: `_get_context_for_model()` prepends web search results with a prominent heading ("Результаты поиска в интернете — ИСПОЛЬЗУЙ ТОЛЬКО ЭТИ ДАННЫЕ") so the reasoning model treats them as authoritative. `reasoning.template` (ru + en) contains explicit rules: "opирайся ТОЛЬКО на предоставленный контекст. Не выдумывай факты."

## Task Cancellation

- **Client**: cancel button (`■`) in streaming messages → POST `/api/cancel_task/{task_id}`.
- **Server**: `cancel_task(task_id)` sets Redis flag `task:cancel:{task_id}` with TTL.
- `_is_task_cancelled(task_id)` checked in every streaming loop iteration.
- **SSE event**: `stream_cancelled` → updates UI.
- **Image gen/edit**: pre/post checks before blocking HTTP calls (`_call_wrapper()`, `generate_image_params()`).
- **Video gen**: background cancel checker thread (`_start_cancel_checker()`) polls every 2s, restarts LTX container on detection. 8 cancel return points across 4 task types.

## Generation Progress

- **SSE events**: `task_progress` (stage labels), `video_step` (progress bar), `image_step` (progress bar), `image_preview` (base64 preview during generation).
- **Server endpoints** in `app/routes/queue.py`: `POST /api/queue/internal/sd_preview`, `POST /api/queue/internal/sd_step`, `GET /api/queue/progress/<task_id>`.
- **Progress persistence**: `_save_progress()` stores in Redis hash (`task_progress:{task_id}`) with TTL 30 min. `_cleanup_progress()` removes on completion.
- **Client restore**: `restoreTaskProgress()` in `events.js` fetches `/api/queue/progress/{taskId}` on SSE reconnect/page reload.

**Stage labels** (Russian): `preparing_gpu`, `analyzing`, `analyzing_image`, `analyzing_prompt`, `generating_video`, `generating_image`, `editing_image`, `loading_reasoning_model`, `capturing_snapshot`.

## Camera Rooms CRUD

- **`app/cameradb.py`** — CRUD for `camera_rooms` table.
- **`app/morph.py`** — pymorphy3 Russian morphological analysis (generates declension forms: nomn, accs, loct).
- **`app/static/js/admin-cameras.js`** — admin UI.
- **DB table**: `camera_rooms` (code TEXT PK, name_forms TEXT[], enabled BOOLEAN, sort_order INTEGER, created_at, updated_at).
- **API endpoints** in `app/routes/admin.py`: `GET /admin/api/cameras`, `PUT /admin/api/cameras/<code>/toggle`, `POST /admin/api/cameras/sync`, `GET /admin/api/cameras/<code>/proxy`.
- **Morphology**: `generate_room_name_forms(name)` generates up to 3 declension forms. Filters adjectives by gender to avoid wrong-gender forms.
- **Router prompt**: `modules/base.py:_build_camera_prompt_section()` dynamically builds camera section from DB with all declension forms.
- **Camera module**: `modules/cam.py` loads rooms from DB, resolves declensions via `get_room_code()`.
- **Migration**: `migrate_name_forms()` in `app/__init__.py` regenerates existing room forms with pymorphy3 on startup.
- **Dependency**: `pymorphy3>=2.0.6`.

## Combined Voice + Image Recording

`app/static/js/chat-recording.js`: if image already attached when voice is recorded, voice stored as `attachedVoiceBlob` instead of replacing `attachedFile`. Preview shows `"image.jpg + 🎤 voice.webm"`.

Server: `_process_transcribe_task()` creates `type: "image"` task when both `image_data` + `voice_record` present.

## Multi-Tab Session Fix

**Problem**: `session_id` was read exclusively from Flask cookie. Flask cookies are shared between all tabs of the same browser. When a user created a new session in one tab and sent a message in another, the message could end up in the wrong session.

**Solution**: Client now sends `session_id` in the request body. Server validates it (UUID v4 + user ownership) and uses it if valid, falling back to Flask cookie for backward compatibility.

- `app/static/js/chat-init.js`: `sendMessage()` includes `session_id: currentSessionId` in both JSON and FormData requests.
- `app/routes/messages.py`: `send_message()` reads `session_id` from request body, validates (UUID v4 + DB ownership), updates Flask session.

## UI Queue Indicators

`chat-queue.js`:
- `fetchQueueStatus()` builds `newInfo` from server data only (no `pendingRequestIds` race guard).
- **Multiple ⚡ prevention**: only one session shows ⚡ at a time — the rest show ⏳ with real queue positions from server.
- **Queue position display**: uses nullish coalescing (`??`) — position 0 (extra processing tasks) shows ⏳ without a number, normal queue positions show ⏳ N.
- **Background tasks invisible**: `fact_extraction_task` and `fact_merge_task` are filtered out by `_BACKGROUND_TASK_TYPES` in `get_user_requests_status()` — they never trigger ⚡ or ⏳ indicators.

**⚡ recovery after task chain**: `events.js` — after every `clearSessionQueue()` call, `setTimeout(fetchQueueStatus, 500)` is scheduled. This polls the server for the next queued task, restoring ⚡ when the next task moves from queue to processing.

## Chat Auto-Scroll

- **`_isLoadingMessages` flag** in `chat-messages.js` prevents N competing async scroll callbacks when loading message history.
- **`isNearBottom()`** threshold = 200px — only auto-scrolls when user is near the bottom of the chat.
- **`scrollToBottom()`** simplified — single `scrollTo()` call with `behavior: 'smooth'`.
- **`overflow-anchor: none`** on chat container CSS — prevents browser from auto-scrolling to anchored element during DOM updates.

## Lazy Loading

All `<img>` and `<video>` elements created with `loading = 'lazy'`.

## llama-swap Configuration

- All llama.cpp models share a single `llm_fast` group with `swap: true`.
- `seen_aliases` set in `generate_yaml()` prevents duplicate aliases when multiple modules share the same GGUF file.
- Config auto-generated from DB at startup into `llama-swap-config/`.
- `include_preload` parameter controls `hooks.on_startup` preload hook: `False` on initial startup (prevents crash from stale on-disk YAML), `True` on admin reload and dry_load.

## Multimodal Models

MUST be in a subdirectory with `mmproj-*.gguf` (e.g. `Qwen3VL-8B-Instruct-Q4_K_M/`).

## Helpers

- `app/circuit_breaker.py` — per-model-type circuit breakers (chat, reasoning, multimodal, embedding)
- `app/resource_manager.py` — centralized VRAM management (see `docs/VRAM_MANAGEMENT.md`)
- `app/llama_swap_config.py` — llama-swap YAML generation
- `app/slm_import.py` — SLM background import
- `app/model_config.py` — model configuration
- `app/config.py` — app configuration (env vars loaded here; both `.env` and `.env.example` must be kept in sync)
- `app/db.py` — database helpers
- `app/events.py` — SSE event publishing
- `app/userdb.py` — user database operations
- `app/validators.py` — input validation
- `app/cli.py` — Flask CLI commands
- `app/cameradb.py` — camera rooms CRUD
- `app/morph.py` — Russian morphology
- `app/utils.py` — shared utilities: `clean_markdown_for_tts()` strips markdown before TTS synthesis, `estimate_tokens()` estimates token count, `chunk_text()` splits text, `_gguf_scalar()` extracts Python scalars from GGUF reader fields, `translate_sd_error()` translates sd.cpp errors