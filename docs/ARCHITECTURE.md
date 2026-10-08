# Architecture — FLAI v12.5

This document describes the internal architecture of FLAI in detail. Read it when modifying core logic, queue, modules, or data flow.

For critical rules and commands, see the root `AGENTS.md`.

> **v10.0 change:** Two-model architecture: multimodal (Qwen3VL-8B-Instruct, always resident, `ttl=0`, `preload: on_startup`) + reasoning (on-demand, `ttl=1`). The standalone "chat" model (Qwen3-4B) was removed. Multimodal = LLM router + text chat + vision (image analysis + editing). After a reasoning task, `_preload_multimodal_sync()` reloads the multimodal model synchronously before responding. VRAM management simplified: only one llama.cpp model resident at a time (multimodal XOR reasoning), except embedding runs on a separate CPU/low-VRAM slot. `ensure_vram_for_reasoning()` evicts multimodal; after reasoning, multimodal is reloaded. `app/queue.py` no longer unloads/reloads multimodal in camera or image-chat paths (always resident). `MODULE_TYPES` reduced to `{multimodal, reasoning, embedding}`.

> **v9.1 change:** Web search module (`modules/search.py`) gained `_fetch_page_content()` — downloads pages with empty SearXNG snippets and extracts readable text via `trafilatura`. Later enhanced: `_fetch_page_content()` runs in parallel via `ThreadPoolExecutor` for short snippets (<300 chars), and instructions softened from «USE ONLY THIS DATA» to «use this data as your primary source» in both `modules/base.py` and prompt templates. `format_results_context()` truncates per-result to 2 000 chars and caps total dynamically via `get_search_context_limit()` (~30% of effective budget, ~11 K chars for 16 K context). `_get_context_for_model()` returns RAG+SLM without history (instead of empty) when budget is exceeded. Depends on `trafilatura>=2.0.0`. Docker volumes simplified: `./app`, `./modules`, `./prompts` now mounted as directories instead of 35 individual file mounts; `PYTHONDONTWRITEBYTECODE=1` added. (Explicit `categories=general,news` was later removed — caused DuckDuckGo rate limiting.)

## Entrypoint & Structure

- **`app/__init__.py:create_app()`** — Flask application factory.
- **Blueprints** (`app/routes/`): `auth`, `chat`, `admin`, `queue`, `tts`, `messages`, `sessions`, `documents`, `backups`, `events`, `debug`, `rlm`, `api_v1` (public OpenAI-compatible API).
- **Modules** (`modules/`): `base/router`, `multimodal`, `sd_cpp`, `cam`, `rag`, `audio`, `tts`, `slm`, `search`, `video`, `rlm`.
- **Background tasks** (`app/tasks/`): `dry_load.py` (model dry-load after admin save; auto-rollback covers both model swaps — restores the fallback model — and context-only changes — restores `context_length`), `health_monitor.py` (crash-loop watchdog).
- **Templates** (`app/templates/`): `admin.html`, `base.html`, `chat.html`, `login.html`.
- **Static**: `app/static/css/` (all CSS), `app/static/js/` (all JS). No inline styles, no CDN.

## Public OpenAI-compatible API (v12.3)

- **Bearer identity is request-local.** `app/routes/api_v1.py:api_token_required` verifies a per-user API key (SHA-256 digest stored by the web API-key panel) and fills `flask.g.api_user` (login, service class, language, style…). The `/v1` blueprint never reads or writes Flask `session`, sets no cookies and is CSRF-exempt.
- **A capability router, not a model server.** Chat is persisted and enqueued exactly like a web message, so the LLM router decides which subsystem answers (RAG/search/history/reasoning/image/video). `model`, `tools`, `response_format` and sampling parameters are accepted and ignored where documented.
- **`app/api_bridge.py`** owns session resolution (`api_conv:<login>:<sha1(user)>` pointers, `metadata.session_id` pinning), enqueue, synchronous/SSE waiting, and the Redis task-owner registry (`api:task:<id>` hash + capped 500-ID per-owner sorted set, `REDIS_RESULT_TTL`); requeued children are followed only with an exact ownership metadata match.
- **Everything heavy goes through the existing queue.** Embeddings/transcriptions run as dedicated `api_*` task types on the fast worker; images, videos, edits and RLM enqueue on the slow worker with the standard GPU lock — no model generation ever runs inside an HTTP request. Media results are served owner-checked via `GET /v1/flai/tasks/{task_id}/content` with realpath containment under `UPLOAD_FOLDER`.
- **Limits and errors.** `API_RATE_LIMIT` is enforced per key owner (OpenAI 429 before enqueue), `API_MAX_CONCURRENT_WAITS` caps synchronous waiters, `API_CORS_ORIGINS` allowlists exact browser origins, and every failure uses the localized OpenAI error envelope starting with `⚠️ `. Full contract: `docs/API.md`; tests: `tests/test_api_v1_*.py`, `tests/test_api_tasks.py`, `tests/test_api_documents.py`, `tests/test_api_rlm.py`, `tests/test_api_sessions.py`, `tests/test_api_inventory.py`.
- **Interactive reference.** flasgger (`flasgger==0.9.7.1`, bundled Swagger UI assets, no CDN) serves the hand-written OpenAPI 3.0.3 spec `docs/openapi-v1.yaml` (24 paths / 29 operations) at `/v1/docs` (UI) and `/v1/openapi.json` (document); both are public — a spec is not data — and are registered in `create_app()` right after the `/v1` blueprint with `rule_filter` disabled so view docstrings are never scanned. `API_V1_SPEC_PATH` is absolute on purpose (a relative `template_file` resolves against `app.root_path`, not the project root), `app/templates/flasgger/head.html` overrides the upstream template that links Google Fonts, and `tests/test_api_docs.py` fails the build on any `/v1` route missing from the spec (and vice versa). The old `docs/openapi.yaml` (internal web/admin API, pre-`/v1`) stays as is and is not served.

## LLM Client

`app/llamacpp_client.py:LlamaCppClient` with two backends:

- **`DirectLlamaBackend`** — direct HTTP calls to `llama-server` at `LLAMACPP_URL`.
- **`LlamaSwapBackend`** — calls via `llama-swap` proxy at `LLAMA_SWAP_URL`.

Selected by `LLAMACP_BACKEND` env var (default: `llama-swap`).

`_ensure_vram()` in `llamacpp_client.py` returns `bool` — every chat/stream call checks VRAM before POST. No model will ever receive 502 due to VRAM — insufficient VRAM returns a proper error message.

## Queue System

`app/queue.py:RedisRequestQueue` — two workers with strict GPU serialization.

**Worker ownership:** workers start only in server processes. `create_app()` calls `RedisRequestQueue(app, start_workers=not _is_cli_process())` — a `flask` CLI process (detected via `sys.argv[0]`) never starts workers. Without this guard a `docker exec flai-web flask <cmd>` invocation became a second, invisible queue consumer (daemon=False worker threads hang the CLI process forever; its logs go to the lost exec stdout). `start_worker()` additionally has a `_workers_started` idempotence guard so a duplicate worker set can never be spawned in one process.

### Fast Worker (mostly CPU; embedding is GPU-light)
- Router classification (multimodal model, always resident)
- Text processing
- Audio (TTS/STT)
- **RAG search only** (embedding + Qdrant, ~500 MB)

All slow-worker GPU tasks are requeued there — the fast worker never runs generation.

### Slow Worker (GPU-heavy)
- Multimodal (Qwen3VL-8B on 12GB+ GPU tiers, Qwen3VL-4B on 8 GB and CPU)
- Text chat / reasoning answers
- SD (Stable Diffusion)
- LTX-Video
- **Reasoning** (Qwen3.6-35B-A3B-UD on GPU tiers; gpt-oss-20b-mxfp4 native MXFP4 in CPU-only mode)
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
- `services/kokoro/` — text-to-speech (higher-quality alternative, selected at deploy time; cold-start warmup thread + RUAccent G2P worker unloaded after `KOKORO_G2P_IDLE_TIMEOUT`)
- `services/superlocalmemory/` — long-term memory (SLM)
- `services/llamacpp/` — llama.cpp servers
- `llama-swap` — Docker image `ghcr.io/mostlygeek/llama-swap:v255-cuda-b10991` (pinned; upstream changed the config format in v243 and the floating `:cuda` tag then broke fresh deployments — see README CUDA table)

**Docker compose profiles**: `with-image-gen`, `with-voice-piper`, `with-voice-kokoro`, `with-rag`, `with-video`, `with-slm`, `with-search`. (`with-voice` is kept as an alias for `with-voice-piper`.)

## Model Lifecycle on a Single Consumer GPU

All llama.cpp models share a single group with `swap: true` in llama-swap. At most ONE model is loaded in VRAM at any time.

**TTLs**:
- `multimodal` = 0 (never unload — stays hot permanently, only swapped when another model needs VRAM)
- `reasoning`, `embedding` = 1s (unload 1 second after response)

Multimodal model preloaded at startup via `hooks.on_startup.preload: ["multimodal"]`. After a reasoning task, `_preload_multimodal_sync()` reloads multimodal synchronously before responding (no cold start on the next request).

SD and LTX-Video use separate GPU contexts.

### Sequence of Models

1. **Multimodal (Qwen3VL-8B, ~5.9 GiB incl. mmproj)** — always resident (TTL=0, preloaded at startup). Serves ALL three roles: LLM router + text chat + vision (image analysis and editing). On an 8 GB GPU tier and in CPU-only mode the lighter Qwen3VL-4B (~2.5 GB + mmproj) is seeded instead. Context length is auto-fit at deployment (`_autofit_context()`, 8192–32768 depending on the GPU/RAM tier, 16384 floor to accommodate vision token counts from dynamic image tiling). Swapped out on demand (e.g. for reasoning).

2. **Reasoning (Qwen3.6-35B-A3B-UD-Q2_K_XL on GPU tiers; gpt-oss-20b-mxfp4 in CPU-only mode)** — loaded on demand for complex queries. MoE 35B (~3B active); 8 GB GPU tiers use partial CPU offload. Context length is auto-fit at deployment; TTL=1s → unloaded 1 second after response. After it finishes, `_preload_multimodal_sync()` reloads the multimodal model synchronously.

3. **Embedding (bge-m3 Q8_0, 0.5 GiB)** — runs on the fast worker (RAG search only). TTL=1s → unloaded 1 second after use.

4. **SD / LTX-Video** — before generation, llama-swap is asked to unload all models via `POST /api/models/unload` (implemented in `resource_manager.py:unload_llamacpp_model()`). This frees ~6 GiB VRAM (multimodal + mmproj). LTX-Video container is **always** restarted after video generation (`_force_restart_ltx_video()`, no rate-limiting) to free CUDA context (~3 GB). Multimodal is then reloaded via `_preload_multimodal_sync()`.

### Example: Video Generation Request
router (multimodal, resident) → [-VIDEO-] → multimodal generates video params (still resident)
→ video pipeline loads (multimodal swapped out / unloaded first) → video generated
→ container restart (CUDA context freed)
→ _preload_multimodal_sync() → multimodal reloaded
→ next user request is instant

### CPU-only mode: Video Parameters

On a CPU-only deployment (`app/queue.py:_plan_cpu_video` → `modules/video.py:plan_cpu_generation`) the requested 768×512×240 is planned against **two** budgets before generation:

- **RAM** — smaller of host free RAM (`MemAvailable`, `resource_manager._detect_available_ram_mb`) and the LTX-Video container's memory cap from `LTX_VIDEO_RAM_LIMIT_MB` (set in `docker-compose.cpu.yml`); the container cap is binding because an OOM there kills the container despite free host RAM.
- **Time** — `LTX_VIDEO_CPU_TIME_BUDGET_S` (default 85% of `LTX_VIDEO_TIMEOUT`) so the generation reliably finishes before the client request times out. `estimate_cpu_generation_time_s()` predicts wall-clock from a linear per-voxel CPU throughput calibration (`CPU_VOXELS_PER_STEP_S=24_000`, ~495 s/step observed for 384×256×120 on a 12-core host), a per-frame VAE decode/assembly cost (`CPU_VAE_SECONDS_PER_FRAME=45`, calibrated from a 256×192×57 run whose VAE decode alone took ~43 min), and a fixed `CPU_TIME_OVERHEAD_S=300` (T5 encode + upscaler + I/O).

The largest of 768×512×240 → 384×256×120 @ 12 fps → 256×192×57 @ 6 fps satisfying BOTH constraints wins; the user is notified of the exact chosen format (or a clear "too slow / not enough memory" error when nothing fits).

### CPU-only mode: Image Parameters

SD generation and editing on CPU (`modules/sd_cpp.py`) halve **both sides** of the resolution before the request is sent to sd-wrapper: 1024×1024 → 512×512 (~4× fewer pixels, ≈~4× faster) so the diffusion run finishes inside `SD_CPP_TIMEOUT` (previously it timed out around step 3/10). Editing also downsizes the source image itself and the target output to the same halved size. A localised notice (`resize_notice` channel, stored as a `system` assistant message) tells the user about the reduced resolution.

### Model Hub downloads and companion files

`app/model_hub.py` displays a multi-part GGUF model as one entry whose size sums every shard. Non-first shards, imatrix files, MTP/draft heads and FastMTP sidecars are auxiliary files, not standalone model choices. `_companion_files()` associates compatible sidecars with the primary file; supported `noMTP` models can select one Q4_0/Q8_0 draft head (Q8_0 default). Selected companions are validated against the repository listing and included in disk-space checks and `.hubmeta`.

Download state is published in Redis hashes (`model_hub:job:<id>`); the `parts` array is JSON-serialized because Redis hashes accept scalar values. `get_job()` falls back to Redis when polling reaches a web process without the local job object. The browser renders active jobs in a dedicated area outside replaceable search results, persists job IDs in `sessionStorage`, and resumes polling after same-tab reloads. Progress totals are grouped whole MB rounded up. On narrow screens, Model Hub controls and tables, and the Models tab's Downloaded models list, scroll horizontally.


## SLM (SuperLocalMemory)

Per-user SQLite databases at `/app/data/slm/{user}/.superlocalmemory/memory.db`.

- **Daemon mode** (`slm serve start`) keeps embedding model in memory permanently.
- `services/superlocalmemory/slm_http.py` proxies requests to daemon at `localhost:8765` (no subprocess per call).
- **Per-user isolation**: recall reads directly from the user's private SQLite table (`atomic_facts`), not from the daemon's shared database.
- **Facts injection** — `_get_context_for_model()` in `modules/base.py` fetches SLM facts first (single `slm.recall(...)` call with `limit × 2`), splits them by `fact_type` (session_specific vs general), measures the real token cost, then fills the remaining budget with conversation history (budget order: query → RAG context → SLM facts → history; when the budget is still exceeded, RAG+SLM is returned without history).
- **Remember** saves to both daemon (shared) and per-user DB (async subprocess).
- **Camera router parser**: uses text after `[-CAMERA-]` marker (room code), NOT `original_query` — preserves compatibility with Russian declensions.
- **Router retry on JSON error** — `process_message()` retries once if the router returns a garbled `{"error": ...}` response.
- SLM facts are injected into prompt context for BOTH multimodal and reasoning models.
- **SLM lazy availability re-check** — `_get_context_for_model()` always calls `slm.get_context()` (no `slm.available` check).
- **SLM dedup** — `_recall_from_user_db()` deduplicates facts by content (score `limit × 3`, returns unique). Configurable via `SLM_RECALL_LIMIT` (default 7).
- **Fact extraction** — background thread (`_extract_facts_bg()`) runs CPU-only after chat responses >20 chars. Extracts facts from the **user's query** (not the model's response) using rule-based pattern matching (`app/slm_rules.py`) — no LLM, no GPU lock. Model self-referential responses ("How can I help?", "Here is your answer") and hallucinated news are filtered out by `_MODEL_RESPONSE_PATTERNS` and `_MODEL_CONTENT_PATTERNS`. Semantic deduplication via `/similarity` endpoint before saving. Wrapped in try/except — failures are logged but never surface to users.
- **Fact merge** — `_process_fact_merge()` runs on background queue during sleep mode. Uses rule-based pipeline: fast_cleanup → edit_distance_merge → fragment_merge → semantic_merge (via `/similarity`) → temporal_decay. No LLM, CPU-only.
- **SLM similarity** — `/similarity` endpoint in `slm_http.py` checks candidate text against existing facts using the daemon's embedding model. Returns cosine similarity score (0.0–1.0). Used by both extraction and merge for deduplication.
- **Temporal decay** — facts older than `SLM_TEMPORAL_DECAY_DAYS` (default 90) with confidence < `SLM_MIN_CONFIDENCE_FOR_DECAY` (default 0.5) are auto-archived during merge.
- **Memories cleanup** — Daemon writes to both `memories` and `atomic_facts` tables, but only `atomic_facts` is read by the system. `_cleanup_memories_for_user()` removes orphaned `memories` rows (no active `atomic_facts`). `_periodic_cleanup()` runs hourly as a daemon thread. `/cleanup-memories` POST endpoint for manual cleanup.
- **Skills list** — `prompts/{ru,en}/skills.txt` is the single source of truth for all capabilities. `format_prompt()` auto-injects `{skills_section}` when the template contains the placeholder.
- Background import on startup via `slm_import_progress` checkpoint table.
- Auto-cleaned on last session deletion (`_cleanup_slm_if_empty()` in `db.py`).
- **Profile lifecycle matches user accounts** — deleting a FLAI account (`delete_user()` in `app/userdb.py`) also permanently removes its SLM daemon profile through the wrapper's `POST /delete-profile` route: temporary switch to the target → GDPR erase (`confirm`-guarded, only operates on the active profile) → restore the previously active profile → delete the profile row. The erasure verdict is checked against the per-table **user-data counters** (`_erasure_user_data_clean()`), not the daemon's `success` flag — `write_commits`/`erasure_receipts` are intentionally immutable system/journal artifacts, so profiles with a write history still wipe fully. Failures are logged and never block the account deletion; `default`, unknown, blank and currently active profiles are refused.
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
3. Calculate history budget: `available_tokens - query_tokens - template_overhead - rag_tokens - slm_tokens`
4. Load conversation history with SQL-level limit (`SESSION_SUMMARY_MAX_FETCH` window)
5. Combine: RAG + SLM facts + rolling session summary + history

- **No hardcoded reserves** — actual fact sizes used throughout.
- Safety margin: configurable via `CONTEXT_SAFETY_MARGIN` (default 0.85) applied on top of `CONTEXT_HISTORY_PERCENT` (default 80).
- Template overhead: `TEMPLATE_OVERHEAD_TOKENS` (default 800).
- Final validation: `_validate_prompt_size()` enforces 95% hard limit.

### v11.3 — token calibration & rolling session summary

- **Real tokenizer calibration** (`app/utils.py`): after every real LLM call the client records `usage.prompt_tokens` into a per-`(model_type, lang)` char/token calibrator. `estimate_tokens()` prefers the calibrated median chars-per-token over the static `TOKEN_COEFFICIENTS` table once ≥3 samples exist, converging the budget onto the actual tokenizer used by the model. Enabled for both backends (Direct `llamacpp` and `llama-swap`), streaming (`stream_options.include_usage`) and non-streaming.
- **Rolling session summaries**: when the token budget trims old history, the trimmed prefix is folded into a compact summary generated by the multimodal model (`prompts/{ru,en}/summarize.template`) and stored in `chat_sessions.summary`/`summary_upto_id`. The stored summary is injected before history, so long sessions keep the conversation thread. Regeneration is serialized per session (module-level guard) and only covers messages newer than `summary_upto_id`.
- History is trimmed strictly by the token budget (no message-count cap); `MAX_HISTORY_MESSAGES` is now a fetch-window hint (default 90).

## Tool Calling System

`app/tools.py` — native OpenAI-compatible tool calling with `--jinja` in llama-server.

**7 tools**:
1. `get_current_time`
2. `calculator` (safe AST eval)
3. `web_search` (SearXNG)
4. `rag_search` (Qdrant)
5. `history_search` (PostgreSQL lexical search across prior sessions)
6. `camera_snapshot`
7. `time_calc` (9 date/time operations via Pendulum)

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
- **Engine roster** — `searxng/settings.yml` enables `google news`, `bing news`, `yahoo news`, `yahoo`, `bing`, `mojeek`, `marginalia`, `presearch`, `qwant`, `yandex`, and `swisscows news` in addition to the default `google cse` and `duckduckgo`, improving resilience when individual engines fail. Engine names/status are restored by the container image build; edits require `docker exec flai-searxng chown` first if the file is root-owned.
- **Retry + soft error** — `_process_search_task()` (fast worker, CPU-only) retries the query once when SearXNG returns 0 results. If both attempts are empty, the user receives a soft localized notification («Search services are temporarily unavailable. Please try again in a few minutes.») instead of a hard «No web search results found» error.
- **Date normalization** — `enhance_query_with_date()` in `modules/search.py` resolves relative date words («позавчера/вчера/сегодня», English equivalents) to absolute dates in the user's timezone (`app.config["TIMEZONE"]`) before POSTing to SearXNG: «Какие ИТ новости были вчера?» → «Какие ИТ новости были вчера (14 сентября 2026)?». Engines otherwise return generic section landing pages instead of dated articles. Queries without relative date words are untouched — this is query normalization, not routing.

### Tavily provider (v12.4)

`SearchModule.search_with_fallback()` is the single entry point for all web
search. When the requesting user has stored a Tavily key
(`app/tavily_keys.py`) and `TAVILY_ENABLED` is true, `_search_tavily()` runs
first (`POST {TAVILY_API_URL}/search`, `Authorization: Bearer`, `search_depth`
from `TAVILY_SEARCH_DEPTH`, `include_raw_content=false`). Tavily results reuse
the `{title, url, content}` contract; snippets shorter than 300 chars are filled
from the page via trafilatura, exactly like the SearXNG path. Relative dates are
not normalized for Tavily — it resolves them natively. Any failure (missing key,
disabled provider, 401/403, 429, timeout, malformed body, zero results) falls
back to `search()`, the unchanged SearXNG implementation. The caller receives
`(results, provider)` so it can log which backend answered.

## Web Crawler (Crawl4AI, v12.4)

Optional sidecar container (`flai-crawler`, profile `with-crawler`) running the
`unclecode/crawl4ai:0.9.4` image (Apache-2.0; includes Playwright + Chromium).
The 0.9.4 server binds loopback unless `CRAWL4AI_API_TOKEN` is set — the token
is wired through compose (crawler env + web `CRAWL_API_TOKEN`) and every client
call carries `Authorization: Bearer`. It serves two capabilities that plain
search does not provide:

- **`read_page(url)`** — native chat tool (fast worker): renders one page in a
  real browser via `POST /md` (fit filter) and returns markdown. Registered
  only when the container is reachable; closes the gap where pasted URLs were
  never fetched. The chat status label is localized («🕸️ Открываю страницу...»)
  through `TOOL_META` + `tool_read_page`.
- **`[-CRAWL-]`** — router category for explicit deep-study intents. Queue type
  `crawl_task` runs on a dedicated single-thread worker (no fast/slow-worker
  slot). **The sidecar deliberately rejects deep-crawl strategy objects on
  untrusted requests**, so the BFS lives in the client (`crawl_site`): pages
  are fetched one by one via `POST /md` (`f=raw` keeps links), same-domain
  links are extracted from the markdown, and a start URL deeper than the
  domain root is confined to its path prefix (a `/docs/` start never drifts
  into the portal's global navigation). Caps: `CRAWL_MAX_PAGES` (50) /
  `CRAWL_MAX_DEPTH` (3) / `CRAWL_TIMEOUT_S` (300 s wall clock) /
  `CRAWL_MAX_PAGE_CHARS` (50k) / `CRAWL_MAX_TOTAL_CHARS` (1 MB). The result is
  concatenated as `## <url>` sections **each truncated to the remaining
  context budget** (`get_search_context_limit()` — the first-page exception
  once produced 50035-char contexts and 25802-token prompts), REPLACES the
  per-user document named after the registrable domain (existing deletion
  chain), and the GPU phase (`_run_document_indexing` + VRAM cleanup) runs
  **under `_gpu_lock`** (embeddings are real GPU inference) before re-queuing
  reasoning with `rag_source="crawler"`. The resulting document is a regular
  user document — ordinary RAG search and the Deep analysis (RLM) mode work
  over it.

### Router rules around URLs

A message with ONE link asking about that page's content («что за проект
<URL>») is neither `[-CRAWL-]` nor `[-SEARCH-]`: it is ordinary chat, and the
`read_page` tool opens the link. Category 6 carries a TOP EXCLUSION with a
literal example — without it the classifier routed such messages to web search
in 2 of 4 runs (search cannot open addresses and answers from strangers'
pages).

### Degradation

| Situation | Behavior |
|---|---|
| Container disabled or down | `read_page` unregistered; `[-CRAWL-]` fails with a localized message (never silently downgraded to light search) |
| Anti-bot block (start page) | `SiteBlockedError` → localized «anti-bot protection — falling back to ordinary search» notice, then the plain web-search path (Tavily → SearXNG) runs with the reason appended to the reasoning query |
| 0 usable pages | Localized soft error suggesting ordinary search |
| Limits reached | Clean stop; collected prefix is indexed |
| Document quota full | Localized quota error; content discarded |

### SSRF posture

`app/crawler_guard.py` validates every URL before it leaves the app: http(s)
only, all resolved addresses must be globally routable, credentials rejected.
Residual risk: the in-browser redirect chain inside the container is validated
only by Crawl4AI's own mechanisms — documented for operators, who can
additionally firewall Docker bridges. Trafilatura remains the first-step
extractor; the crawler is the second step for pages that need a real browser.

## Router Classification & Session Context (v12.1)

The classifier lives in `modules/base.py:process_message()` (`temperature=0.1`) and reads the category prompt from `prompts/{ru,en}/base_text.template`.

- **Action-time principle.** The template opens with an «ACTION TIME» section that decides first whether the request is about a **past** action (past-tense verbs: «смотрели», «обсуждали», «присылал», «вчерашние кадры») — routed to `[-HISTORY-]` — or about an action to perform now/in the future. Action categories (camera, image, video, …) explicitly never capture questions about the past; camera is worded as «show the **current** snapshot», «see the room state **right now**».
- **Session micro-context.** `process_message()` accepts `recent_context`; `BaseModule.build_router_context()` (called from `_route_text_action()` in `app/queue.py` before each message is classified) builds it from the newest messages returned by `get_session_recent_history()` in `app/db.py` (the same generation-marker pair filter as `get_session_text_history`, the just-sent message excluded, each message capped at `ROUTER_CONTEXT_MSG_CHARS` chars) plus up to `ROUTER_SLM_FACTS` SLM long-term-memory facts. This lets a follow-up like «did we look at the camera images?» route to history instead of firing the camera again.
- Env vars (defaults in `app/config.py`): `ROUTER_CONTEXT_MESSAGES` (6), `ROUTER_CONTEXT_MSG_CHARS` (240), `ROUTER_SLM_FACTS` (2).

## Conversation History Search

`modules/history.py` implements the `history_search` native tool and the server-side `[-HISTORY-]` router path.

- `search_history()` searches prior sessions with PostgreSQL Russian, English, and simple text-search configurations, ranks matches by the number of matching terms, and bounds indexed message text to prevent large generated code messages from exceeding PostgreSQL's `tsvector` size limit.
- Message content is serialized JSON; only text parts are returned to the model, not attached image/media payloads.
- Router lookups exclude the active session and current message. Broad history-overview requests use stored session summaries when available and otherwise sample first/last user messages from each prior session.
- Search supports Russian and English lexical queries in either UI profile. It does not translate queries across languages.
- `HISTORY_SEARCH_LIMIT`, `HISTORY_MAX_RESULTS_CHARS`, and `HISTORY_MAX_MESSAGE_CHARS` configure result and indexing limits.

## Streaming Reasoning

`modules/base.py:generate_reasoning_response_stream()` yields tokens one-by-one via `_stream_chat()`.

`app/queue.py:_process_reasoning_request()` uses `generate_reasoning_response_stream()` instead of `process_reasoning()`. Tokens are published via `_publish_stream_token()`.

- **Server-side** `_strip_thinking_tags()` in `app/queue.py` handles three patterns: (1) ` thinking... response` blocks are removed entirely; (2) `<|channel|>analysis<|message|>...<|end|>?` (reasoning) is stripped entirely — `<|end|>` is optional to avoid deleting the entire response when gpt-oss-20b omits it; (3) `<|channel|>commentary<|message|>...<|end|>` (actual answer) is **unwrapped** — tags removed, inner content kept. Malformed `<|channel|>...` without `<|message|>` is stripped. **`app/llamacpp_client.py:_process_stream_chunk()`** performs an unconditional buffer flush on `_thinking_active` transition to prevent token loss between `analysis` and `commentary` blocks. `_strip_generic_reasoning()` removes plain-text chain-of-thought (e.g. "Analyze Persona:", "Final Answer Generation:"); it lives in `queue.py` (a wrapper over `llamacpp_client._strip_generic_reasoning()`) and is applied in `_process_reasoning_request()`.
- **Client-side** `_stripThinkingTags()` in `events.js` mirrors server logic and is applied during streaming (`<|channel|>...` tags and generic reasoning), with `_stripGenericReasoning()` stripping generic reasoning patterns in real time.
- **Repetition-loop detection** — `app/llamacpp_client.py` routes all streamed content through a `_LoopGuard` (`_repetition_cutoff`: scan window 4096 chars, block = last 200 chars, cycle = ≥3 equal-gap repetitions). The guard holds back the last 1500 chars so a detected loop is cut before reaching the user (`⚠️ …прерван из-за зацикливания` / English equivalent). **Adaptive hold (slow streams):** the hold is sized in chars, so on a CPU box (~1.6 tok/s) a typical 600–1400 char answer never exceeds it and would arrive as one block after generation finished. Both `_LoopGuard` and `_ReasoningStreamFilter` therefore shrink their hold adaptively: after 40 `feed()` calls (many small feeds = slow stream) the hold linearly shrinks by 30 chars per extra feed, flooring at 300 chars. Fast GPU streams (few large chunks) keep the full hold-back; loop detection is unaffected (it needs 600+ chars of history, and the post-stream safety net cuts anything the smaller hold let through). On clean stream end both backends flush in a fixed order — `_stream_buffer` remainder (through `_loop_guard.feed()`), `_reasoning_stream.flush()`, `_loop_guard.flush()` — so short answers (< hold-back) are never dropped or reordered. A server-side safety net, `strip_repetition_loop()` in `_process_reasoning_request()`, cuts loops missed in streaming and retries once.
- **Thinking-phase status** — the reasoning model streams no content tokens during `reasoning_content` (thinking), so between "model loaded" and "first content token" the queue would otherwise show nothing. Both backends accept a `status_callback` and fire it with `"generating"` right after the completion POST returns (model loaded, SSE headers back); `_process_reasoning_request()` turns it into the `reasoning_thinking` stage («Обдумываю ответ...»), which the frontend ticks with a per-second counter until the first content token removes the indicator.

**Web search context**: `_get_context_for_model()` prepends web search results with a prominent heading ("Web search results — use this data as your primary source.") so the reasoning model treats them as authoritative; `reasoning.template` (ru + en) contains the same softened rule ("use them as primary source"). (Historically the instruction was the stricter "USE ONLY THIS DATA".)

## RLM Deep Analysis (v12.1)

An explicit **"Deep analysis" toggle** in the chat UI (`chat.html` `#rlm-toggle`; `sendRlmAnalysis()` in `chat-init.js` branches off the normal send flow) submits the current question + selected documents to `POST /api/rlm/analyze` (`app/routes/rlm.py`). Documents are picked by clicking them in the documents panel (`rlmSelectedDocs` in `chat-documents.js` syncs the hidden `#rlm-docs` multi-select and highlights picks with a green `.rlm-selected` frame + `✓`); an image can be attached alongside the question. If the toggle cannot start — no documents and no image, or an image without a question — `sendMessage()` clears the checkbox and falls through to the **normal** send flow. The route accepts `multipart/form-data` (`session_id` + `doc_ids` JSON + `text` + optional `file`), enforces authentication, session + document ownership, validates quota/downscales the image, persists the user message (text + image) so it survives page reload, and returns `202` with `task_id`/`position`/`user_message_id`/`resize_notice`.

### Task orchestration

`rlm_analysis` is a slow-worker GPU task: `_classify_queue_fast()` routes it to the slow queue, `_get_model_for_task()` maps it to `reasoning`, and the slow worker holds `_gpu_lock` for the whole task. `_process_rlm_task()` in `app/queue.py`:

1. requests `ensure_vram_for_reasoning()`, then marks the GPU busy for the whole analysis (`ResourceManager.mark_rlm_busy()` / `mark_rlm_idle()`; `_rlm_busy` extends `is_gpu_busy()`, so the v11.5 watchdog-skip guard covers RLM too) and publishes the `loading_reasoning_model` stage;
2. builds the corpus from the **selected documents only** (text extracted via `extract_text_from_file()`) — not RAG;
3. if the request carries an attached image (`file_data` in the task payload), requests `ensure_vram_for("multimodal")` and describes it through `MultimodalModule.describe_image_for_rlm()` (new `prompts/{ru,en}/rlm_image.template`), adding the detailed text description to the corpus as a `«Изображение (file_name)»` document; a failed or empty description returns the localised «Unable to recognize the image for deep analysis» error; after the image phase `ensure_vram_for_reasoning()` is re-checked (the multimodal model gets unloaded);
4. rejects the corpus before any GPU work when its total size exceeds the `RLM_MAX_CORPUS_CHARS` (default 50 000 000 chars) OOM cap — the localized error tells the user to select fewer/smaller documents and the model is never loaded; then runs a reasoning actor loop (`modules/rlm.py:RlmModule.run()`) capped at `RLM_MAX_STEPS` (default 18). The per-host step allowance comes from the resource ladder `_resource_step_budget()` (24 GB+→18, 16 GB→12, 12 GB→10, 8 GB→8, CPU/<8 GB→6); the context window never cuts steps — `_obs_trunc_for_context()` compresses per-step observations instead (down to an 800-char floor) so the trajectory fits 95% of the reasoning `context_length` minus a 1000-token reserve, and a window too small even for a minimal one-observation-per-step trajectory still degrades to 1 step instead of dying with «Request too long». Each step calls the resident reasoning model with the four tools `python` / `llm` / `web_fetch` / `final`; `final(answer)` (or a plain text answer) ends the loop. **The final step offers only the `final` tool** — the template stays tool-aware (withholding ALL tools made some models, e.g. Qwen3.6, print the call as plain text that leaked to the user as «internal dialogue»), but `python`/`llm`/`web_fetch` are unreachable; a `parse_leaked_tool_calls()` guard parses printed `<function=…><parameter=…>` blocks back into structured calls (a leaked `final` yields the answer, a leaked `python` runs and the analysis continues), and a non-final tool executed on the last step grants ONE bounded final-only finalize call so the gathered observation reaches the answer instead of dying at «step limit reached». `llm()` is a sub-model call capped at `RLM_SUB_MAX_TOKENS` (1024); `web_fetch()` runs a SearXNG search (top 3 results, snippet-based) through the **parent** process, capped at `RLM_WEB_MAX_FETCHES` (5) per analysis. Cancellation (Redis flag) is checked each step. The answer **language** is pinned via `{response_language}` in `prompts/{ru,en}/rlm.template` plus localized `build_user_prompt()` and `broker_llm()`, and an anti-premature-`final` guard rejects a `final()` call for a multi-file corpus before any `python` inspection has run — the run continues with a corrective tool message instead of closing early. The templates also tell the actor that `re`/`math` are already available in the sandbox (no `import`, no `lambda`) and that a failing python call must be fixed, not repeated.

The whole analysis is **one GPU task**: the reasoning model is JIT-loaded once and kept resident across the actor's turns (its `ttl=1s` reload cost is accepted). On completion the per-step trace (step/tool/args/observation) is stored to the Redis key `rlm_trace:<task_id>` (TTL 3600) and the answer is saved via `_save_and_respond()` with `extra` metadata `model_type="rlm"` / `rlm_trace_task_id` / `rlm_steps`. The DB message carries `model_type="rlm"` (its own 🔬🧠 header emoji), and the trace also persists (as a diagnosable key) when the run ends in an error.

### Sandbox isolation

`app/rlm_sandbox.py:RlmSandbox` executes model-generated code in a persistent forked child process:

- code is AST-whitelisted before execution — `validate_code()` rejects imports, function/class definitions, `with`/`lambda`/`yield`, and dunder access (`FORBIDDEN_NODES` / `FORBIDDEN_NAMES` / `FORBIDDEN attribute`); the child namespace is limited to `SAFE_BUILTINS`;
- `RLM_CODE_TIMEOUT` (default 15 s, refreshed on each IPC callback) bounds every snippet; on timeout the child is killed. rlimits: address space (2 GB), CPU seconds, zero processes/files/core;
- the child has **no direct network or filesystem access** — `llm()` and `web_fetch()` are IPC callbacks (`SandboxBroker`) that round-trip to the parent, which executes the tool and streams the result back into the sandbox namespace;
- `final(answer)` raises an internal signal that ends the run with the answer; observations are truncated to `RLM_OBS_TRUNC` (4000 chars).

### Config, stages & UI

Env vars (`app/config.py`, mirrored in `.env` / `.env.example`): `RLM_ENABLED` (true), `RLM_ACTOR_MODEL` (reasoning), `RLM_MAX_STEPS` (18 — hard ceiling; the ladder and platform decide the real budget), `RLM_TASK_TIMEOUT` (0 — auto-derive the wall-clock deadline from the step budget and platform: GPU 120+90×steps, CPU 240+300×steps seconds; `-1` disables, any positive value is used as-is; on expiry the run ends with the localized «task exceeded the time limit» error and the partial trace is saved), `RLM_CODE_TIMEOUT` (15 s), `RLM_OBS_TRUNC` (4000 — upper bound; the context-fitted value may be lower), `RLM_SUB_MAX_TOKENS` (1024), `RLM_WEB_MAX_FETCHES` (5), `RLM_MAX_CORPUS_CHARS` (50 000 000).

Progress stages stream via `task_progress`: `loading_reasoning_model`, then `rlm_reading` («Читаю документы...» / «Deep analysis: reading documents»), `rlm_step` («🔬 Глубокий анализ: фаза %s», with a per-step counter via `STAGE_COUNTER_KEYS`), `rlm_searching_web` (reuses the existing search label), `rlm_submodel`, and finally `rlm_finalizing`. On completion `appendRlmTraceBlock()` in `events.js` attaches a collapsible «🔬 Deep analysis (N steps)» summary to the last assistant message — the full per-step trace stays in the Redis key and is not rendered yet.

## Document Image Processing and Scanned PDF OCR (v12.1)

`describe_document_image` handles uploaded image documents and PDFs that contain no extractable text. For a scanned PDF, the worker reads the page count with `pdfinfo`, renders each page to a 1536-pixel JPEG with `pdftoppm`, and asks the multimodal model to describe the page and transcribe readable text. For an uploaded image, it describes the image directly. The output is saved beside the original as `.recognized_text` and queued for normal RAG indexing, making scanned pages, labels, and diagrams searchable. Poppler utilities are installed in the web image.

## HTML Message Preview

`GET /api/html-preview/<message_id>` (`app/routes/messages.py`) serves the first HTML code block from an authenticated user's own chat message in a separate response with a preview-specific CSP. This avoids the chat page's strict CSP blocking local previews. `_ensure_three_importmap()` repairs generated Three.js pages that import CDN addon modules without an import map: it injects a JSON-serialized map and rewrites compatible CDN imports to `three` aliases; pages with an existing map are left untouched.

## Task Cancellation

- **Client**: cancel button (`■`) in streaming messages → POST `/api/cancel_task/{task_id}`.
- **Server**: `cancel_task(task_id)` sets Redis flag `task:cancel:{task_id}` with TTL.
- `_is_task_cancelled(task_id)` checked in every streaming loop iteration.
- **SSE event**: `stream_cancelled` → updates UI.
- **Image gen/edit**: pre/post checks before blocking HTTP calls (`_call_wrapper()`, `generate_image_params()`).
- **Video gen**: background cancel checker thread (`_start_cancel_checker()`) polls every 2s, restarts LTX container on detection. 8 cancel return points across 4 task types.

## Generation Progress

- **SSE events**: `task_progress` (stage labels), `video_step` (progress bar), `image_step` (progress bar), `image_preview` (base64 preview during generation).
- **Chat/reasoning stages** — `routing` → `searching_documents`/`searching_web` → `loading_reasoning_model` → `reasoning_thinking` → streaming indicator. Stage→label maps: `events.js:STAGE_LABEL_KEYS` (+ `STAGE_COUNTER_KEYS` for `results`/`chunks` counts with `%s` placeholders), localized labels in `chat.html` TRANSLATIONS. Counters and the per-phase elapsed-seconds timer are client-side; the frontend keeps one progress element per session because phase chains cross task ids (fast-worker search → requeued reasoning task).
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

Server: `_process_transcribe_task()` creates `type: "image"` task when both `image_data` + `voice_record` present. The pairing works in BOTH attachment layouts: voice in the dedicated `voice` field pairs with the image from `file_data`; voice in the legacy `file` slot (v12.4 multi-attachment flow) pairs with the first image among the extra parts, and the full image list travels in `request_data["images"]` so the vision model receives every attached picture. Tests: `tests/test_voice_image_pairing.py`.

## Clipboard Image Paste

`app/static/js/chat-init.js` adds a `paste` listener on the message input. Behavior priorities:

1. **Text only in clipboard** → default text paste (handler returns early, browser handles it).
2. **Image only** → `preventDefault()`, blob is wrapped into a generated `File` named `pasted_YYYYMMDD_HHMMSS.<ext>` (extension derived from MIME type, `jpeg` → `jpg`), attached as `attachedFile`, and the standard file preview (name + size) is shown.
3. **Both text and image** (e.g. copying a picture from a browser carries an HTML/text snippet alongside the bitmap) → the image wins: `preventDefault()` fires and the text is discarded.

No backend changes: the pasted file travels through the same `FormData` upload path as a file picked via the attach button. Non-image clipboard content (files, PDFs) is ignored.

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

## Per-Request Token Usage Counters

Every assistant message header shows real billed tokens between the ⏱️ duration and the 🚀 tokens-per-second segments: `…| ⏱️ 12.4 s | 🔢 (▲45 ▼1 234) ток | 🚀 0,8 ток/с |` (▲ output first, ▼ input, `tokens_unit` msgid — ru «ток» / en «tok»).

**Backend** (`app/queue.py` + `app/utils.py`):
- `_process_request()` opens ONE thread-local usage account per task (`begin_usage_account()` in `app/utils.py` — `begin/record/finish/current` family) for every LLM task type; bookkeeping-only types (`index_document`, `reindex_all_embeddings`, `fact_extraction_task`, `fact_merge_task`) are excluded and `_process_single_task()` finally drops leftovers.
- Every LLM call accumulates its real `prompt_tokens`/`completion_tokens` through `_record_prompt_tokens()` in `app/llamacpp_client.py` → `record_usage_for_current()`.
- Requeued tasks (reasoning, image_gen, video) carry the fast worker's half-account inside `request_data` (`request_id`, `submitted_at`, `usage_accum`; `_requeue_*_task()`), `_process_request()` re-seeds it with the bias on the slow worker.
- `_save_and_respond()` consumes the account (`finish_usage_account()`): the result dict and `messages.prompt_tokens` (new INTEGER column, `app/database.py` migration) both carry the totals; `process_time` is overridden to the full request duration (`submitted_at` → finish). `consume_usage=False` keeps the account open for multi-message tasks (camera snapshot + description pair; the description consumes the merged totals).
- Unknown/absent values (legacy rows with `prompt_tokens=NULL`) are stored as NULL and render only the known side.

**Frontend** (`chat-utils.js:tokenStatsHTML()`, shared by history render and live finalize):
- `tokenStatsHTML()` renders only the known sides (e.g. `🔢 (▲45) ток` when input is unknown); empty/zero totals render nothing.
- The `window.displayMessage` wrapper in `chat-init.js` must forward ALL positional parameters of the wrapped function — it previously dropped the 19th (`promptTokens`), hiding input tokens in every SSE path. Guard: `tests/test_js_signatures.py` asserts wrapper signature == wrapped signature and positional forwarding.

## Admin User Token Totals

`GET /admin/api/users` returns `outgoing_tokens` and `incoming_tokens` for each non-admin account. These are computed from messages joined through the user's `chat_sessions`: outgoing (from the user's perspective) sums `messages.prompt_tokens`, and incoming sums `messages.completion_tokens`. The admin users table displays the columns immediately after Sessions; both are sortable numeric fields. No schema change is needed. Full backups include `chat_sessions` and `messages`, so both inputs to these aggregates are restored; users-only backups include only `users` and intentionally do not preserve chat history or token totals.

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
- `app/db.py` — database helpers; `save_message()` returns the real message id via `INSERT … RETURNING id` (`cursor.lastrowid` is always 0 on PostgreSQL/psycopg2)
- `app/events.py` — SSE event publishing
- `app/userdb.py` — user database operations
- `app/validators.py` — input validation
- `app/cli.py` — Flask CLI commands
- `app/cameradb.py` — camera rooms CRUD
- `app/morph.py` — Russian morphology
- `app/utils.py` — shared utilities: `clean_markdown_for_tts()` strips markdown before TTS synthesis, `estimate_tokens()` estimates token count, `chunk_text()` splits text, `_gguf_scalar()` extracts Python scalars from GGUF reader fields, `translate_sd_error()` translates sd.cpp errors; per-request usage accounts (`begin_usage_account()` / `record_usage_for_current()` / `finish_usage_account()`) feed the message-header token counters
