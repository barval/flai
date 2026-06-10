# AGENTS.md — FLAI v8.9 (model protection: 3-tier VRAM/RAM, dry-load, watchdog)

## Commands (exact)

```bash
# Install
pip install -e ".[dev]"          # full dev deps (ruff, mypy, types)
pip install -e ".[test]"         # just pytest deps

# Lint
ruff check .

# Type check
mypy app/ modules/               # CI runs with `|| true` — does not block

# Test
pytest                           # all tests
pytest -m unit                   # markers: unit, integration, e2e, slow, requires_db, requires_redis
pytest -m "not slow"
pytest --cov=app --cov=modules --cov-report=html
pytest tests/test_admin_routes.py

# Translations
pybabel extract -F babel.cfg -k _tr -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations
pybabel compile -d translations  # after editing .po files

# Admin tasks
flask admin-password <pass>       # set/reset admin password
docker exec flai-web flask cleanup-uploads  # remove orphaned files from uploads/
docker exec flai-web flask admin-password <pass>  # in container
docker exec flai-web flask migrate-messages-format  # convert old plain-text service msgs → JSON {prefix, text}
docker exec flai-web flask migrate-messages-format --dry-run  # preview without writing
docker exec flai-web flask import-history-to-slm [--force] [user_id]  # import messages to SLM

# Whisper model cache
data/hf-cache/                           # HF Hub cache for whisper ASR model
ls data/hf-cache/hub/models--Systran--faster-whisper-medium/  # Systran/faster-whisper-medium model

# Dev server (0.0.0.0:5000, debug=True)
python wsgi.py

# Production (gunicorn 2 workers, 900s timeout)
gunicorn -c gunicorn_config.py wsgi:app

# Docker compose profiles: with-image-gen, with-voice, with-rag, with-video, with-slm
docker compose -f docker-compose.gpu.yml --profile with-image-gen --profile with-voice --profile with-rag --profile with-video --profile with-slm up -d
docker compose -f docker-compose.gpu.yml logs -f web

# Load test
locust -f tests/load/locustfile.py --host http://localhost:5000
```

## Architecture & conventions

- **Entrypoint**: `app/__init__.py:create_app()` → returns Flask app. Blueprints in `app/routes/` (auth, chat, admin, queue, tts, messages, sessions, documents, backups, events, debug). Modules in `modules/` (base/router, multimodal, sd_cpp, cam, rag, audio, tts, slm). Background tasks in `app/tasks/` (dry_load, health_monitor). Templates in `app/templates/` (admin.html, base.html, chat.html, login.html).
- **LLM client**: `app/llamacpp_client.py:LlamaCppClient` with two backends — `DirectLlamaBackend` (direct llama-server) or `LlamaSwapBackend` (via llama-swap proxy). Selected by `LLAMACP_BACKEND` env var.
- **Queue**: `app/queue.py:RedisRequestQueue`. Two workers with strict GPU serialization:
  - **Fast worker** — CPU-only operations: router (chat model), text, audio, RAG **search** (embedding + Qdrant only, ~500 MB). The chat model (2.5 GiB) stays hot in VRAM.
  - **Slow worker** — all GPU-heavy operations: multimodal (Qwen3VL-8B), SD, LTX-Video, **reasoning** (gpt-oss-20b), **RAG generation** (via reasoning model). Strictly sequential — only one GPU task runs at a time.
  - **VRAM guard** (`_wait_for_vram`) — before any multimodal/SD/Video call, blocks until at least 6 GiB VRAM is free (polls `nvidia-smi` every 2s, times out after 60s).
  - **Synchronous VRAM polling** (`_poll_vram`) — `_resolve_use_gpu()` and `ensure_vram_for_llm()` call `_poll_vram()` synchronously before reading `available_vram_mb` (was updated every 60s, causing stale data and OOM). After every `unload_llamacpp_model()`, a wait loop verifies VRAM is actually freed (up to 30s).
  - **VRAM guard for reasoning** (`ensure_vram_for_reasoning`) — unloads llama.cpp models and waits (up to 60s) for SD/Video to free VRAM before loading gpt-oss-20b (~10 GiB).
  - Tasks are HMAC-signed JSON.
- **DB**: PostgreSQL only via `app/database.py:get_db()` context manager (psycopg2 RealDictCursor). `DATABASE_URL` required. Tables: user_sessions, chat_sessions, messages, documents, session_visits, model_configs, user_storage, slm_import_progress, gguf_models_cache, camera_rooms.
- **Helpers**: `app/circuit_breaker.py`, `app/resource_manager.py`, `app/llama_swap_config.py`, `app/slm_import.py`, `app/model_config.py`, `app/config.py`, `app/db.py`, `app/events.py`, `app/userdb.py`, `app/validators.py`, `app/cli.py`, `app/cameradb.py`, `app/morph.py`, `app/tasks/dry_load.py`, `app/tasks/health_monitor.py` — llama-swap config auto-generated from DB at startup into `llama-swap-config/`. Background SLM import + dry-load (after admin model save) + crash-loop watchdog all run as daemon threads.
- **Docker mounts**: `./data/` → `/app/data`, `./services/llamacpp/models/` → `/models:ro`, `/var/run/docker.sock` for GPU detection.
- **Config**: Model configs in DB (`model_configs` table). `.env` values are fallback defaults only. Admin panel at `/admin`.
- **Multimodal models**: MUST be in a subdirectory with `mmproj-*.gguf` (e.g. `Qwen3VL-8B-Instruct-Q4_K_M/`).
- **LLM backend modes**: `LLAMACP_BACKEND=llama-swap` (default in .env.example) uses llama-swap at `LLAMA_SWAP_URL=http://flai-llamaswap:8080`. `LLAMACP_BACKEND=llamacpp` (direct) uses `LLAMACPP_URL=http://flai-llamacpp:8033`.
- **VRAM** (`app/resource_manager.py`): Centralized VRAM management via two methods:
  - `get_vram_needed_mb(model_type)` — computes VRAM needed from GGUF metadata (file_size, block_count), DB config (context_length), and n_gpu_layers. Formula: `file_size × (ngl/block_count) × moe + ctx_size × kv_factor + overhead`. No hardcoded constants.
  - `ensure_vram_for(model_type)` — unloads ALL models, flushes CUDA cache, polls /running + nvidia-smi (60s timeout). Returns False (never proceeds) if VRAM insufficient. Used by ALL model types: chat, reasoning, multimodal, embedding.
  - `_ensure_vram()` in llamacpp_client.py now returns `bool` — every chat/stream call checks VRAM before POST.
  - No model will ever receive 502 due to VRAM — insufficient VRAM returns a proper error message.
- **VRAM 3-tier classification** (`app/routes/admin.py:_classify_model_fit`): three-tier model-fit classification used in the admin panel to prevent OOM when saving a model config:
  - `good` — `vram_needed ≤ 85% × total_vram` → save allowed, full ngl.
  - `cpu_offload` — VRAM insufficient but (file_mb - gpu_weights) ≤ 70% × system_ram - 2 GB → save allowed, ngl degraded to fit.
  - `impossible` — neither VRAM nor RAM can hold the model → save blocked with 400 error.
  - `unknown` — model not in `gguf_models_cache` (no GGUF metadata) → save blocked; user must click "Refresh models" first.
  - `arch_max_ctx` from `gguf_models_cache.context_length` is the architectural cap (Qwen3 = 262144, gpt-oss = 131072). Upper limit is now dynamic, no hardcoded 32768.
- All llama.cpp models share a single `llm_fast` group with `swap: true` in llama-swap. At most ONE model is loaded in VRAM at any time. TTLS: chat=600s (always hot), multimodal/reasoning/embedding=0s (unload immediately after response). SD and LTX-Video use separate GPU contexts. Three VRAM tiers (8/12/16+ GB) adjust `n_gpu_layers` and resolution caps.

  **Model lifecycle on a single consumer GPU:**
  1. **Chat (Qwen3-4B, 2.5 GiB)** — preloaded at startup and stays hot (TTL=600s). Default model for router and direct responses. Swapped out on demand when another model from the group is needed. Reloaded automatically on the next chat request.
  2. **Multimodal (Qwen3VL-8B, 5 GiB)** — loaded on demand (camera, image analysis, video param gen). TTL=0 → **unloaded immediately** after the response is sent. VRAM freed for subsequent tasks.
  3. **Reasoning (gpt-oss-20b, MXFP4, 10 GiB)** — loaded on demand for complex queries. TTL=0 → unloaded immediately after response.
  4. **Embedding (bge-m3 Q8_0, 0.5 GiB)** — TTL=0 → unloaded immediately after use.
  5. **SD / LTX-Video** — before generation, llama-swap is asked to unload all models via `POST /api/models/unload` (implemented in `resource_manager.py:unload_llamacpp_model()`). This frees ~3-4 GiB VRAM (chat). After generation, the next user request reloads chat automatically.

  **Sequence for a video generation request:**
  `router (chat) → [-VIDEO-] → multimodal loads (chat swapped out) → multimodal generates video params → multimodal unloads (TTL=0) → video pipeline loads (full VRAM available) → video generated → video pipeline unloads → next user request reloads chat`.
- **SLM (SuperLocalMemory)**: Per-user SQLite databases at `/app/data/slm/{user}/.superlocalmemory/memory.db`. Daemon mode (`slm serve start`) keeps embedding model in memory permanently; `services/superlocalmemory/slm_http.py` proxies requests to daemon at `localhost:8765` (no subprocess per call). **Per-user isolation**: recall reads directly from the user's private SQLite table (`atomic_facts`), not from the daemon's shared database. **Chat model** uses fast direct SQLite read (`ORDER BY created_at DESC`). **Reasoning model** uses full semantic search via subprocess `slm recall` (falls back to direct SQLite if no embeddings). Remember saves to both daemon (shared) and per-user DB (async subprocess). **Camera router parser**: uses text after `[-CAMERA-]` marker (room code), NOT original_query — preserves compatibility with Russian declensions (гостиная → в гостиной). **Router retry on JSON error** — `process_message()` retries once if the router returns a garbled `{"error": ...}` response. SLM facts are injected into prompt context for BOTH chat and reasoning models (alongside conversation history). **Router retry on JSON error** — `process_message()` retries once if the router returns a garbled `{"error": ...}` response. **SLM lazy availability re-check** — `_get_context_for_model()` always calls `slm.get_context()` (no `slm.available` check), the method has its own lazy re-check. **SLM dedup** — `_recall_from_user_db()` in `slm_http.py` deduplicates facts by content (score `limit × 3`, returns unique). Configurable via `SLM_RECALL_LIMIT` (default 7). Background import on startup via `slm_import_progress` checkpoint table. Auto-cleaned on last session deletion (`_cleanup_slm_if_empty()` in `db.py`). **Per-user SLM files are owned by appuser (UID 1000)** matching the web container — `start.sh` runs `chown -R appuser:appuser` on the shared volume. Fact count visible in admin panel column.
- **`_tr()` / `self._()` format strings**: Flask-Babel 4.0.0 `gettext()` uses `%`-formatting (`string % variables`), NOT `str.format()`. Passing `{status}` kwargs directly to `gettext()` silently returns the unformatted string. Always call `gettext(key)` without kwargs, then apply `result.format(**kwargs)` manually. See `app/llamacpp_client.py:26` and `app/mixins.py:9` for the correct pattern. **pybabel extraction**: always use `-k _tr` flag when extracting, since `_tr` is a custom keyword not recognized by default: `pybabel extract -F babel.cfg -k _tr -o translations/messages.pot .`
- **Style**: All CSS in `app/static/css/`, JS in `app/static/js/`. No inline styles, no CDN (all assets bundled). Comments/logs in English. User-facing strings via Flask-Babel (`translations/{en,ru}/LC_MESSAGES/messages.po`). Add new keys to both `.po` files.
- **UI queue indicators**: `chat-queue.js` — `fetchQueueStatus()` builds `newInfo` from server data only (no `pendingRequestIds` race guard). **Multiple ⚡ prevention**: only one session shows ⚡ at a time — the rest show ⏳ with real queue positions from server. **Queue position display**: uses nullish coalescing (`??`) — position 0 (extra processing tasks) shows ⏳ without a number, normal queue positions show ⏳ N.
- **⚡ recovery after task chain**: `events.js` — after every `clearSessionQueue()` call, `setTimeout(fetchQueueStatus, 500)` is scheduled. This polls the server for the next queued task, restoring ⚡ when the next task moves from queue to processing.
- **Lint config** (pyproject.toml): ruff line-length=120, select E/W/F/I/N/UP/B/SIM, ignore E501/B008/PTH. `__init__.py` per-file-ignore F401. mypy target 3.11, ignore-missing-imports, excludes tests/ and translations/.
- **Security**: Path traversal checks in `api/files/<path>`. Session ownership validated. CSRF on all forms (`WTF_CSRF_TIME_LIMIT=28800`, synced with session). `session.permanent = True` at login (8h idle timeout). Secrets in `.env` only.
- **Streaming reasoning**: `modules/base.py:generate_reasoning_response_stream()` yields tokens one-by-one. `app/queue.py:_process_reasoning_request()` publishes via `_publish_stream_token()`. Server-side `_strip_thinking_tags()` removes `<tool_call>` and `<|channel|>analysis<|message|>...<|end|>` blocks before DB save. Client-side `_stripThinkingTags()` in `events.js` handles both complete and incomplete (streaming) tags.
- **Task cancellation**: `cancel_task(task_id)` sets Redis flag `task:cancel:{task_id}` with TTL. `_is_task_cancelled(task_id)` checked in every streaming loop iteration. Client sends POST to `/api/cancel_task/{task_id}`.
- **Generation progress**: `task_progress` (stage labels), `video_step`, `image_step` (progress bars), `image_preview` (base64 preview). Stored in Redis hash `task_progress:{task_id}` with TTL 30 min. Client restores via `GET /api/queue/progress/<task_id>`.
- **DOMPurify**: `purify.min.js` loaded in `chat.html`. All `marked.parse()` output goes through `DOMPurify.sanitize()` before DOM insertion in `events.js:finalizeStreamedMessage()` and `chat-messages.js:displayMessage()`.
- **Camera rooms CRUD**: `app/cameradb.py` provides CRUD for `camera_rooms` table. `app/morph.py` generates Russian declension forms (nomn, accs, loct) via pymorphy3. `modules/base.py:_build_camera_prompt_section()` builds router prompt dynamically from DB. `modules/cam.py` loads rooms from DB and resolves declensions via `get_room_code()`.
- **Combined voice + image**: `app/static/js/chat-recording.js` stores voice as `attachedVoiceBlob` when image already attached. Server creates `type: "image"` task for combined processing.
- **Lazy loading**: All `<img>` and `<video>` elements created with `loading = 'lazy'`.

## Testing

- Fixtures in `tests/conftest.py`: `test_app` (isolated app + temp dirs), `client` (Flask test client), `runner` (CLI runner)
- External services are ALWAYS mocked: Redis (`redis.from_url`), llama.cpp (`app.llamacpp_client.LlamaCppClient`), Qdrant (`modules.rag.QdrantClient`)
- **DB mode**: mock by default (no `DATABASE_URL`). In CI (`DATABASE_URL` set) — real PostgreSQL with `TRUNCATE` between tests via `test_app` teardown.
- **Background workers**: `RedisRequestQueue` threads are stopped via `stop_workers(timeout=3)` in `test_app` teardown to prevent pytest hang.
- Available markers: `unit`, `integration`, `e2e`, `slow`, `requires_db`, `requires_redis`
- Example: `pytest -m "not slow"` to skip slow tests

## Localization & Language
- All **code comments** and **log messages** must be in English.
- All **user-facing messages** (UI, notifications, errors) must use the selected user language (i18n).
- Always keep translation files (`messages.po`) up‑to‑date and complete.
- For Russian, the file `deploy-ru.sh` is the only place where Russian comments are allowed.
- **Every** user-facing string MUST be wrapped in `_()` / `self._()` / `gettext()`. Raw `str(e)` must NEVER be returned to the user.
- **When adding or modifying error messages**, ALWAYS verify that corresponding translation keys exist in both `translations/en/LC_MESSAGES/messages.po` and `translations/ru/LC_MESSAGES/messages.po`. Run `pybabel extract -k _tr && pybabel update && pybabel compile` to sync.

## Dependencies & External Resources
- The project must run **fully offline** after model/voice downloads.
- No external scripts, CDN links, or remotely loaded modules in production.
- All CSS must reside in `.css` files; all JavaScript in `.js` files (no inline styles/scripts).
- External dependencies (models, voices) must be documented with size, license, and download instructions.
- All Python dependencies must have **open-source licenses** (MIT, BSD, Apache 2.0, MPL, or equivalent). Proprietary or copyleft (GPL/AGPL) dependencies are prohibited. Verify license before adding.
- **pymorphy3** is used for Russian morphological analysis of camera room names (generates declension forms).

## Cleanliness & Dead Code
- No unused files, dead code, or unused CSS/JS.
- Every import must be used; every translation key must appear in the UI.
- Run `pybabel extract -k _tr` / `pybabel update` / `pybabel compile` after modifying translatable strings.
- Remove any leftover debug prints, commented-out blocks, or obsolete TODOs.

## Documentation
- README files in English and Russian must always reflect the current state of the project.
- Each version must include release notes (“What’s new”).
- Provide a one‑command deployment script (English and Russian versions) that handles:
  - Environment setup
  - Model downloads
  - Component builds
  - Full project launch
- List all used models, their licenses, and approximate sizes in README.

## Code Quality
- No typos, syntax errors, or unreachable code.
- Lint with `ruff check .` and type‑check with `mypy app/ modules/`.
- Always write clean, self‑documenting code; add comments only when necessary.

## Release Documentation Process

When releasing a new version, update these files in order:

### 1. README.md and README-ru.md — "What's New" section
- Located in the Architecture section (under `### What's New in vX.X`)
- List only the **most impactful changes** from the new version
- Format: `| **Feature name** | Brief description |`
- Keep entries concise (1-2 lines per feature)

### 2. README.md and README-ru.md — "Roadmap -> Completed" section
- Located at the bottom of the Roadmap section (under `### ✅ Completed` / `### ✅ Завершено`)
- Add **only the most significant new features, major changes, and critical bug fixes**
- Do NOT add minor fixes, test changes, or internal refactoring
- Format: `- **Feature name** — brief description`

### 3. AGENTS.md — version section
- Update the version title in `## v8.X — ...`
- Document technical details of new features (architecture, algorithms, parameters)
- Update the "Known issues" section — move fixed items to the version section

### 4. Git tags
- Create a git tag for the new version: `git tag v8.X`
- Push tags: `git push origin v8.X`

## Known issues (fix on sight)

- **mypy** `app/utils.py`: `Module has no attribute "parse_rtf"` — striprtf stub issue. Fix: `# type: ignore[attr-defined]`. Other 19 mypy errors in `app/utils.py` (numpy `arr[0]` narrowing + `bytes(val.tolist())` arg-type) **fixed in v8.9** via `_gguf_scalar()` helper.
- **Unit test speed**: CamModule has 5×2s init retries, making test_cam.py ~10s per fixture.
- **Load tests** (`tests/load/`) excluded from pytest collection (require locust fixtures).
- **Pre-existing excluded tests**: ~~`test_backups.py` (8 tests with KeyError 'babel' — missing Flask-Babel init in local fixture)~~ — **FIXED in v8.9** by adding `Babel(flask_app)` to `tests/test_backups.py:app` fixture. ~~`test_backups.py::TestBackupRestore::test_restore_backup` (shutil.copytree FileExistsError on data/slm)~~ — **FIXED in v8.9** via `dirs_exist_ok=True` in `app/routes/backups.py:restore_backup()`. `test_base_module.py::test_parse_router_response_image_marker` (router response format changed). These are not blocking CI.

## VRAM fixes & RAG improvements (v8.8+)

### VRAM Problem statements
- **CUDA OOM on video generation**: `_wait_for_vram()` only polled nvidia-smi without checking if llama.cpp models were unloaded, leading to fragmented memory claims and OOM when loading Qwen3VL-8B (~5GiB)
- **HTTP 502 on reasoning queries**: `ensure_vram_for_reasoning()` returned False on timeout but code continued execution, attempting to load gpt-oss-20b (~10GiB) into insufficient memory
- **Video OOM persisted**: After multimodal generates params (~5GB), LTX-Video pipeline (~8GB) tries to load while multimodal is still in VRAM → total exceeds 15.47GB GPU → OOM
- **Hardcoded VRAM constants**: All VRAM estimates were hardcoded (2500/5000/15000/2000 MB), causing 502 errors when model was changed or VRAM was fragmented

### VRAM Implemented solutions
1. **`_wait_for_vram_full()` redesign**: Changed from `≥80% GPU threshold` to `video_needed + 3GB buffer` — the 80% threshold was impossible to meet on a 15GB GPU after unloading a 5GB multimodal model (max free was ~10GB, need 12.4GB)
2. **Timeout increase 30→60s**: Both in queue.py and video.py; CUDA memory deallocation is async and needs more time
3. **No "proceeding anyway"**: When VRAM wait times out, return error instead of proceeding into OOM
4. **Buffer increase +500→+3000MB**: In `_resolve_use_gpu()` for safety margin against CUDA fragmentation
5. **Dynamic VRAM estimation via GGUF metadata**: `_estimate_model_vram()` computes VRAM from `file_size_mb * (ngl/block_count) + ctx_size * kv_factor + overhead`. No hardcoded constants — uses actual model file size, layer count, and context window from DB.
6. **Real VRAM measurement & storage**: `measure_model_vram()` captures actual VRAM consumption after each successful model load and stores it in `model_vram_estimates` table with measurement count and context-length metadata.
7. **Admin panel displays measured vs estimated VRAM**: Shows "✓ VRAM: X MB / Y MB — measured (N measurements)" or "ℹ VRAM: ~X MB — estimated" with color-coded percentage bars.
8. **Per-model-type circuit breakers**: Separate CB for chat, reasoning, multimodal, embedding. One model's failures don't block another.
9. **Retry for reasoning on 502**: LlamaSwapBackend now retries reasoning requests once on 502, with automatic model degradation on first failure.
10. **Phantom measurement fix — PK schema change**: `model_vram_estimates` table PK changed from `(module)` to `(module, model_name)`. Each model now gets its own row — switching to a new model in the same module no longer inherits phantom measurements from the old model. Idempotent migration in `init_db()` via `DO $migrate$` block. `get_vram_estimate(module, model_name=None)` accepts optional `model_name` for exact match (legacy path for ltx-video). `upsert_vram_estimate()` queries by `(module, model_name)`. Admin endpoint (`/admin/api/model-estimate`) passes `model_name` to `get_vram_estimate()`. Bonus: fixed `UPDATE WHERE module` bug — was overwriting measurements of ALL models in the module, now scoped to `(module, model_name)`.
11. **LTX-Video unload optimization (Plan A+B)**: Three-part fix in `resource_manager.py:unload_video_pipeline()`:
   - **A1 — Pre-flight check**: `GET /v1/vram_info` before HTTP unload. If `pipeline_loaded=false`, return `True` immediately — skips 3×POST + 8s×8 polling (~28s saved per call).
   - **A3 — 30s result cache**: `_last_ltx_unload_at` timestamp prevents double-call within 30s (queue.py calls unload twice per image request: once explicitly, once via `ensure_vram_for`).
   - **A2 — Reachable success condition**: Changed from `free_before + 3000` to `min(total - 1000, free_before + 3000)`. Original condition was unreachable when `free_before > total - 3000` (our case: 15229 > 16311 - 3000).
   - **A4 — Docker restart on 3 consecutive timeouts**: `_maybe_restart_ltx_video()` restarts `flai-ltxvideo` container via Docker socket (`POST /containers/flai-ltxvideo/restart`) after 3 consecutive `ReadTimeout` exceptions. Rate-limited to 1 restart per 5 minutes.
   - **Tests**: `tests/test_resource_manager_ltx_unload.py` — 11 tests across 4 classes (Preflight, Cache, SuccessCondition, DockerRestart).

### RAG Problem statements
- **Router sends knowledge questions to reasoning instead of RAG**: "Сколько лет Валерию Барсукову?" classified as `[-REASONING-]` instead of `[-RAG-]` — no examples of Q&A about people/documents
- **Streaming path skips RAG entirely**: `_process_text_task_stream` goes directly to `_requeue_reasoning_task()` without calling `_try_rag_answer()`
- **Strict threshold too high**: 0.7 falls back for unconfigured environments, filtering out relevant chunks
- **Reasoning model blind**: No document context passed to gpt-oss-20b prompt
- **RAG prompt encourages hallucination**: rag.template said "answer on your own if context is empty" + "don't write 'no info'", causing the model to fabricate answers or respond "no available information" instead of reading provided context
- **RAG context lost on failure**: When `generate_answer()` returned None, raw document chunks were discarded; reasoning model got empty context even if Qdrant found relevant chunks

### RAG Implemented solutions
1. **Router template updated**: Added category 5 with explicit examples for document/person/age/biography queries → `[-RAG-]`
2. **RAG call added to streaming reasoning path**: Before requeuing to slow worker, tries RAG first
3. **Strict threshold lowered 0.7→0.5**: Higher recall for semantic search
4. **RAG context in reasoning prompt**: `process_reasoning()` now accepts `rag_context` parameter; appended to prompt templates
5. **RAG retry in `_process_reasoning_request`**: Before loading reasoning model, tries RAG once more with `strict=True`
6. **RAG prompt fixed**: Changed from "answer on your own" to "use ONLY the provided context. If context doesn't contain the answer — honestly say you cannot find it." Prevents hallucination.
7. **Raw chunks passed to reasoning model**: On RAG failure, `_process_reasoning_request` calls `rag.search()` directly to retrieve raw chunks (no LLM filtering) and passes them as `rag_context` to the reasoning model. Guarantees the reasoning model always sees document content.

### Files modified
- `app/database.py`: Added `model_vram_estimates` table. Added `get_vram_estimate()`, `upsert_vram_estimate()` helpers.
- `app/routes/admin.py`: `_estimate_model_vram()` now uses dynamic formula from GGUF metadata (file_size × ratio + ctx_size × kv_factor + overhead). Reads measured VRAM from `model_vram_estimates` table. Writes computed estimate to DB. Response includes `measured_vram_mb`, `measurement_count`, `context_length`.
- `app/resource_manager.py`: Added `measure_model_vram()` — captures VRAM after model load and stores in DB. Enhanced `ensure_vram_for_reasoning()` with CUDA cache clearing and tighter /running verification.
- `app/llamacpp_client.py`: Calls `measure_model_vram()` after each successful model response. Per-model-type circuit breakers (separate CB for chat, reasoning, multimodal). Fixed `_ensure_vram` — replaced `except Exception: pass` with proper logging. Added retry for reasoning on 502. Degrade model on every failure (not just circuit breaker open).
- `modules/video.py`: Buffer +500→+3000, timeout 30→60s, no "proceeding anyway". Added CUDA cache flush after generation.
- `app/queue.py`: Added `_gpu_lock`, `_log_gpu_state_before_op()`, `_check_vram_ready()`, `_unload_llamacpp_models()`, CUDA cache flush in `_unload_video_pipeline()`, RAG in streaming path, RAG retry in reasoning task, raw chunk context from Qdrant on RAG failure. `_process_rag_task` / `_process_rag_task_stream` replaced `rag.generate_answer()` → `rag.search()` + `_requeue_reasoning_task(rag_context=...)`. `_requeue_reasoning_task` accepts optional `rag_context`. `_process_reasoning_request` reads pre-computed `rag_context` from `request_data`.
- `modules/base.py`: `process_reasoning()` accepts `rag_context` parameter
- `prompts/ru/rag.template`: Fixed — removed "answer on your own" instruction, added strict "use ONLY context" directive
- `prompts/en/rag.template`: Same fix
- `prompts/ru/base_text.template`: Added RAG category for person/document queries
- `prompts/en/base_text.template`: Same RAG category
- `prompts/ru/reasoning.template`: Added `{rag_context}` placeholder
- `prompts/en/reasoning.template`: Added `{rag_context}` placeholder
- `app/static/js/events.js`: `finalizeStreamedMessage` renders file attachments AND error messages in streaming responses
- `app/static/js/chat-init.js`: `sendMessage()` includes `session_id: currentSessionId` in request body for multi-tab safety
- `app/static/js/admin-models.js`: `updateMemoryEstimation()` now handles `status: "measured"` — shows measured VRAM with measurement count and ctx, and `status: "estimate"` with color-coded percentage.
- `app/routes/messages.py`: `send_message()` reads `session_id` from request body, validates (UUID v4 + DB ownership), updates Flask session
- `app/db.py`: Added `file_data` to SQL SELECT, replaced `suppress(Exception)` with logging. `update_session_title()` uses `_("New session")` instead of hardcoded string (localization fix).
- `translations/*.po`: RESTORED from v8.7-SLM baseline (1574+ EN / 1581+ RU entries). Added new translation keys: VRAM measurements, footer_text, response_style_* labels, "Your requests / Total requests"
- `docker-compose.gpu.yml`: Removed broken .mo volume mounts; Docker image now compiles translations correctly during build
- `tests/test_llama_swap_config.py`: Fixed `test_no_group_for_default` (multimodal now in `llm_fast` group) and `test_generates_valid_yaml` (`swap: true` is default, not `swap: false`)
- `tests/test_utils.py`: Fixed `test_resize_image_if_needed_large` — function signature changed from `(max_width, max_height)` to `(max_size, quality)`
- `tests/test_video_module.py`: Fixed 6 tests — added `mock_rm_instance.estimate_video_vram_needed.return_value = 8500` and `available_vram_mb = 12000` for VRAM check loop added in v8.8
- `app/tasks/dry_load.py` (NEW): background dry-load test after admin saves a model. Sends a tiny completion via llama-swap, waits for `/running` to show the model (30s timeout), then unloads. On failure — rolls back to `FALLBACK_MODELS[module]`. `FALLBACK_MODELS` = `{chat: Qwen3-4B-MXFP4, reasoning: gpt-oss-20b, multimodal: Qwen3VL-8B, embedding: bge-m3}`. Daemon thread.
- `app/tasks/health_monitor.py` (NEW): crash-loop watchdog. Every 60s polls llama-swap `/running`, sends a tiny health check to each loaded model, tracks failures in a 5-min sliding window. 3 failures in window → auto-rollback to fallback model. `start_watchdog(app)` called from `create_app()` after llama-swap init. Daemon thread.
- `app/tasks/__init__.py` (NEW): package marker.
- `app/routes/admin.py`: new helper `_classify_model_fit()` — 3-tier VRAM/RAM classification using GGUF cache. Extended `model_vram_estimate` endpoint response with `tier`, `can_save`, `ngl_recommended`, `tier_message`, `system_ram_mb`, `arch_max_ctx`. Server validation in `update_model_config` blocks `tier=impossible` (defense in depth). `schedule_dry_load()` called after successful `signal_reload()`.
- `app/validators.py`: comment now notes that upper bound is enforced dynamically in `admin.py:update_model_config` using `gguf_models_cache.context_length`. No more hardcoded 32768.
- `app/__init__.py`: starts `start_watchdog(app)` after SLM import, only when `LLAMACP_BACKEND=llama-swap`.
- `app/static/js/admin-models.js`: `updateMemoryEstimation()` renders a colored tier indicator (green/yellow/red) below the existing memory hint. Disables the Save button on `tier=impossible` or `tier=unknown`. Suggests `ngl_recommended` in the input placeholder for yellow tier. `validateModelConfig()` blocks submission if Save button is disabled.
- `tests/test_classify_model_fit.py` (NEW): 11 tests — small model → good, medium model → cpu_offload, huge model → impossible, unknown model → unknown, arch_max_ctx propagation, .gguf extension stripping, ngl proportionality, edge cases.
- `tests/test_dry_load.py` (NEW): 10 tests — thread spawning, empty model skip, FALLBACK_MODELS coverage, _trigger_load (200/500/network), _check_running (found/not-found/error).
- `tests/test_health_monitor.py` (NEW): 12 tests — failure recording, sliding window eviction, per-module isolation, _clear_failures, _get_running, _try_health_check, start_watchdog daemon thread.
- `tests/test_resource_manager_ltx_unload.py` (NEW): 11 tests across 4 classes (Preflight, Cache, SuccessCondition, DockerRestart) — pre-flight skip, 30s cache, reachable condition, Docker restart trigger.
- `tests/test_vram_estimates.py` (NEW): 10 tests across 3 classes (Upsert, GetEstimate, AdminFilter) — phantom measurement isolation, per-model PK, legacy ltx-video path, admin endpoint model_name filter.
- `app/llamacpp_client.py`: Defensive `return` after `for attempt` loop (mypy Missing return fix). `# type: ignore[no-any-return]` on `_tr()`.
- `app/queue.py`: `process_time` type widened to `float | dict[str, float]` in `_save_and_respond()` and `_build_success_response()`. `current_time_str` now uses fallback `or get_current_time_in_timezone_for_db()` to eliminate `str | None` propagation. Local `sid` variable for `set.add()` to satisfy mypy.
- `app/utils.py`: `scanned` annotated as `dict[str, Any]`. `striprtf.parse_rtf` marked `# type: ignore[attr-defined]`. `gettext` returns marked `# type: ignore[no-any-return]`.
- `app/db.py`: `parsed["text"]` return marked `# type: ignore[no-any-return]`.
- `app/mixins.py`: `_()` return marked `# type: ignore[no-any-return]`.
- `app/routes/admin.py`: `psutil.virtual_memory()` return marked `# type: ignore[no-any-return]`.
- `app/routes/auth.py`: `key_func` lambda now returns `str` guaranteed via `or "unknown"` fallback.
- `app/__init__.py`: `app.modules.get()` marked `# type: ignore[attr-defined]`.
- `modules/base.py`: Added `isinstance(router_response, str)` guard before `.strip()` to prevent `AttributeError` on dict response.
- `modules/multimodal.py`: `# type: ignore[assignment]` on `Image` vs `ImageFile` reassignment (2 places).
- `modules/slm.py`: `# type: ignore[no-any-return]` on `resp.json().get(...)` returns (3 places).
- `modules/video.py`: `resized_info` annotated as `dict[str, Any]`. `# type: ignore[assignment]` on `Image` vs `ImageFile` reassignment (2 places).
- `translations/{en,ru}/LC_MESSAGES/messages.po`: 5 new keys — `✓ Fits in VRAM: {vram} MB / {total} MB`, `⚠ Partial CPU offload: {ngl}/{total_layers} layers on GPU, {cpu} on RAM. ~5-10× slower.`, `✗ Model cannot be loaded. Needs {needed} MB RAM (file + KV cache), available {total} MB.`, `Model metadata not found. Run 'Refresh models' first.`, `model_cannot_be_saved`.

### Error message prefix fix
All error messages displayed to users MUST start with "⚠️ ". `_build_error_response()` adds this prefix automatically. However, error strings from `call_llamacpp()` (e.g., "GPU memory unavailable", "HTTP error 500") were returned as plain strings through `process_reasoning()`, `generate_chat_response_stream()`, and `rag.generate_answer()` — ending up in `_save_and_respond()` without the "⚠️ " prefix. Fixed by adding `_is_llm_error_string()` helper and routing detected errors through `_build_error_response()` in all affected code paths: `_process_reasoning_request`, `_process_text_task`, `_process_text_task_stream`, and RAG answer handling in both `_process_text_task` and `_process_text_task_stream`.

### Translation system fix
Removed .mo volume mounts that were overriding correct compiled translations with incomplete versions. Docker now properly compiles all translations at build time. All site features work in both Russian and English profiles.

### Queue position fix
Removed `pendingRequestIds` race guard from `chat-queue.js` that was overwriting server-provided queue positions with hardcoded `1`. Queue positions now come exclusively from server data. Fast worker now also acquires `_gpu_lock` for GPU tasks. Chat model (Qwen3-4B) included in GPU lock — was excluded previously (`_ensure_vram` skipped unload for chat).

### RAG architecture fix
**Problem:** `_process_rag_task` and `_process_rag_task_stream` on the fast worker called `rag.generate_answer()` which made **direct HTTP calls** to llama-swap to load the reasoning model (~13.6 GiB). This bypassed the queue's GPU serialization. When LTX-Video pipeline was still loaded from a previous video task (in a separate container, ~8 GiB VRAM), the reasoning model couldn't fit in VRAM → "GPU memory unavailable" error.

**Solution:** RAG on the fast worker now does **only search** (`rag.search()` — embedding + Qdrant, ~500 MB) and requeues to the slow worker via `_requeue_reasoning_task(rag_context=...)`. The slow worker handles VRAM management (`ensure_vram_for_reasoning()`), unloads LTX-Video, loads the reasoning model, and generates the answer.

**New flow:**
```
Fast worker: [-RAG-] → rag.search() (embedding + Qdrant) → _requeue_reasoning_task(rag_context=...)
Slow worker: ensure_vram_for_reasoning() → unload LTX-Video → load reasoning model → generate answer
```

**Key changes:**
- `_process_rag_task` / `_process_rag_task_stream`: replaced `rag.generate_answer()` → `rag.search()` + `_requeue_reasoning_task(rag_context=...)`
- `_requeue_reasoning_task`: new optional `rag_context` parameter passed in `request_data`
- `_process_reasoning_request`: reads `rag_context` from `request_data` if present, skips redundant search

### Multi-tab session fix
**Problem:** `session_id` was read exclusively from Flask cookie (`session["current_session"]`). Flask cookies are shared between all tabs of the same browser. When a user created a new session in one tab and sent a message in another, the message could end up in the wrong session due to cookie race conditions.

**Solution:** Client now sends `session_id` in the request body. Server validates it (UUID v4 + user ownership) and uses it if valid, falling back to Flask cookie for backward compatibility.

**Key changes:**
- `app/static/js/chat-init.js`: `sendMessage()` includes `session_id: currentSessionId` in both JSON and FormData requests
- `app/routes/messages.py`: `send_message()` reads `session_id` from request body, validates (UUID v4 + DB ownership), updates Flask session
- `app/db.py`: `update_session_title()` uses `_("New session")` instead of hardcoded `"New session"` (localization fix)

### Model protection (3-tier VRAM/RAM system)
**Problem:** Admin panel allowed saving any model in the dropdown without any hardware fit check. Common failure modes: (1) model > VRAM but `compute_llamacpp_config` silently degrades ngl → model works at 0.5 tok/s; (2) ctx > 32768 hardcoded limit (or worse, ctx > model architectural max) → KV cache explosion → OOM; (3) model file not on disk → llama-server crash loop → 502; (4) crash loop after a bad save degrades service until manual intervention.

**Solution:** 5-layer protection with 3-tier classification:

| Tier | Condition | UI | Server | Result |
|------|-----------|-----|--------|--------|
| 🟢 `good` | `vram_needed ≤ 85% × total_vram` | Green "Fits in VRAM" | Save OK | Full ngl on GPU |
| 🟡 `cpu_offload` | `vram_needed > 85%` AND `(file - gpu_weights) ≤ 70% × ram - 2GB` | Yellow "Partial CPU offload, ~5-10× slower" | Save OK with auto-degraded ngl | ngl recomputed to fit VRAM |
| 🔴 `impossible` | `(file - gpu_weights) > 70% × ram - 2GB` | Red "Cannot be loaded" | **400 block** | ngl=0, model can't fit anywhere |
| ⚠ `unknown` | Model not in `gguf_models_cache` | Orange "Run Refresh models first" | **400 block** | No metadata to compute fit |

**Threshold formula (v8.8+):**
```python
TIER_VRAM_GOOD_PCT = 0.85     # 85% of total VRAM is "good" budget
TIER_RAM_SAFETY_PCT = 0.70    # 70% of system RAM allowed for model + KV
TIER_RAM_HEADROOM_MB = 2048   # 2GB reserved for OS + other processes
```

**Layered protection:**
1. **UI hint** (`app/static/js/admin-models.js:updateMemoryEstimation`): on model/ctx change, fetches `/admin/api/model-estimate` and displays colored tier indicator. Save button is **disabled** when `can_save=false`.
2. **Server validation** (`app/routes/admin.py:update_model_config`): before saving, calls `_classify_model_fit()`. If `tier=impossible` or `tier=unknown` → returns 400 with `tier_message`. Defense in depth (UI is bypassable).
3. **Dry-load + auto-rollback** (`app/tasks/dry_load.py`): after successful `signal_reload()`, schedules a background thread that:
   - Sends tiny completion to llama-swap to trigger model load
   - Polls `/running` for 30s waiting for model
   - On success: unloads test instance (next user request reloads)
   - On failure: calls `_rollback()` to restore `FALLBACK_MODELS[module]`
4. **Crash loop watchdog** (`app/tasks/health_monitor.py`): 60s polling loop:
   - Reads llama-swap `/running` for active models
   - Sends tiny health check to each
   - Records failures in 5-min sliding window
   - On 3 failures in window → auto-rollback to fallback
5. **File size + context validation** (existing `gguf_models_cache` + new arch_max_ctx):
   - `file_size_mb` cached on model scan
   - `arch_max_ctx` from GGUF `context_length` field — replaces hardcoded 32768
   - Server validates `ctx_requested ≤ arch_max_ctx` before save

**New API response fields** in `/admin/api/model-estimate`:
```json
{
  "tier": "good | cpu_offload | impossible | unknown",
  "can_save": true | false,
  "ngl_recommended": 28,
  "tier_message": "✓ Fits in VRAM: 3109 MB / 16311 MB",
  "system_ram_mb": 31694,
  "arch_max_ctx": 262144
}
```

**Fallback models** (used by dry_load + watchdog):
```python
FALLBACK_MODELS = {
    "chat":       "Qwen3-4B-Instruct-2507-MXFP4_MOE",
    "reasoning":  "gpt-oss-20b-mxfp4",
    "multimodal": "Qwen3VL-8B-Instruct-Q4_K_M",
    "embedding":  "bge-m3-Q8_0",
}
```

### Monitoring commands
```bash
watch -n 1 nvidia-smi          # Real-time VRAM tracking
docker logs flai-web --tail 50 | grep GPU  # Log GPU-related events
grep "RAG\|reasoning\|router" docker/logs/flai-web.log  # Debug RAG flow
docker logs flai-web --tail 100 | grep -E "watchdog|dry_load"  # Model protection events
curl -s "http://localhost:5000/admin/api/model-estimate?model=Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf&module=chat&ctx_size=8192" | jq '{tier, can_save, ngl_recommended, tier_message}'  # Test tier classification
```

## GPU Queue Management — CRITICAL RULES

1. **Tasks run strictly sequentially on GPU.** Fast worker MUST acquire `_gpu_lock` for any task that uses GPU (chat, multimodal, embedding, reasoning). Slow worker already uses `_gpu_lock`. NEVER allow two GPU tasks to run concurrently.

2. **VRAM is unconditionally cleaned between every GPU task.** After each task completes, ALL llama.cpp models are unloaded, video pipeline is unloaded, and CUDA cache is flushed. No "predictive" logic — the next task always starts with clean VRAM.

3. **Degradation happens BEFORE model load, not after failure.** `compute_llamacpp_config()` iteratively reduces `n_gpu_layers` until the model fits in available VRAM. If a model doesn't fit even with 0 layers on GPU, the task returns an error instead of crashing with OOM.

4. **VRAM timeout varies by context.** `ensure_vram_for()` (resource_manager.py) uses a 15-second wait. `_wait_for_vram()` (queue.py) uses 30 seconds. `_wait_for_vram_full()` (queue.py) uses 60 seconds. If VRAM isn't freed within the timeout, the task returns an error instead of proceeding into OOM.

Also:
- NEVER make ANY changes to files without direct user approval. Each file change (create, edit, delete) requires explicit plan approval. Exception: only when the user explicitly said "do it" or "execute".
- Hardcoded query filters at the Python level (without LLM) are STRICTLY FORBIDDEN. All query classification and routing MUST go through the LLM router model. Do not add pattern matching, keyword lists, or any deterministic logic to bypass the router for specific queries.
- **All error messages displayed to users MUST start with "⚠️ ".** `_build_error_response()` adds this prefix automatically. For code paths that bypass it (e.g., string errors from `call_llamacpp()`), use `_is_llm_error_string()` check and route through `_build_error_response()`.
- **RAG generation NEVER runs on the fast worker.** `rag.generate_answer()` must NOT be called from `_process_rag_task` or `_process_rag_task_stream`. The fast worker only runs `rag.search()` (embedding + Qdrant). Answer generation via reasoning model happens exclusively on the slow worker via `_requeue_reasoning_task()`. This prevents GPU contention with LTX-Video pipeline.

## GPU Requirement

FLAI REQUIRES an NVIDIA GPU with at least 8 GB VRAM and 16 GB system RAM. CPU-only mode is not supported — LLM inference, SD image generation, and LTX-Video all depend on CUDA. The project automatically adapts to available VRAM (8/12/16+ GB tiers), adjusting model offloading, resolution, and model selection accordingly.

## v8.9 — Video frame policy, streaming, camera CRUD, progress bars, thinking tags

### Video frame policy (replaces v8.8 cap-only approach)

- **Default: 240 frames** (10 sec @ 24 fps), full 768×512 landscape.
- **Capped to 120 frames** at 512×512 ONLY when:
  - `total_vram_mb < 10000` (8/10 GB tier GPU), OR
  - `available_vram_mb < 6000` (12+ GB tier with fragmented VRAM after multimodal unload)
- `prompts/{en,ru}/create_video.template`: JSON default `num_frames: 240`. Instruction text says "use 240 unless user asks for short/5 sec".
- `modules/video.py:generate_video`: applies cap with logging (`"VRAM tier 8GB: capped..."` or `"VRAM soft-cap (available=X MB): reduced..."`).
- `modules/multimodal.py`: warning threshold `weight > free * 10` (240 frames = 92 weight, 6000 MB free = no spurious warning; fires only for extreme requests like 1000+ frames at 4K).
- `ltx_wrapper.py`: `num_frames_padded = ((nf - 2) // 8 + 1) * 8 + 1` — both 120→121 and 240→241 are padded by +1 frame.

### Streaming reasoning

- `modules/base.py:generate_reasoning_response_stream()` — streaming generator for reasoning model responses. Yields tokens one-by-one via `_stream_chat()`.
- `app/queue.py:_process_reasoning_request()` now uses `generate_reasoning_response_stream()` instead of `process_reasoning()`. Tokens are published via `_publish_stream_token()`.
- Server-side `_strip_thinking_tags()` in `queue.py` removes thinking/reasoning blocks before saving to DB.
- Client-side `_stripThinkingTags()` in `events.js` removes `<tool_call>` and `<|channel|>analysis<|message|>...<|end|>` blocks during streaming display.

### Camera rooms CRUD

- **New files**: `app/cameradb.py` (CRUD for `camera_rooms` table), `app/morph.py` (pymorphy3 Russian morphological analysis), `app/static/js/admin-cameras.js` (admin UI).
- **DB table**: `camera_rooms` (code TEXT PK, name_forms TEXT[], enabled BOOLEAN, sort_order INTEGER, created_at, updated_at).
- **API endpoints** in `app/routes/admin.py`: `GET /admin/api/cameras`, `PUT /admin/api/cameras/<code>/toggle`, `POST /admin/api/cameras/sync`, `GET /admin/api/cameras/<code>/proxy`.
- **Morphology**: `generate_room_name_forms(name)` generates up to 3 declension forms (nomn, accs, loct) for Russian room names. Filters adjectives by gender to avoid wrong-gender forms.
- **Router prompt**: `modules/base.py:_build_camera_prompt_section()` dynamically builds camera section from DB with all declension forms.
- **Camera module**: `modules/cam.py` loads rooms from DB (`_load_rooms_from_db()`), resolves declensions via `get_room_code()`, provides `get_all_rooms_with_forms()` for router prompt.
- **Migration**: `migrate_name_forms()` in `app/__init__.py` regenerates existing room forms with pymorphy3 on startup.
- **Dependency**: `pymorphy3>=2.0.6` added to `requirements.txt` and `pyproject.toml`.

### Generation progress bars

- **SSE events**: `task_progress` (stage labels), `video_step` (progress bar), `image_step` (progress bar), `image_preview` (base64 preview during generation).
- **Server endpoints** in `app/routes/queue.py`: `POST /api/queue/internal/sd_preview`, `POST /api/queue/internal/sd_step`, `GET /api/queue/progress/<task_id>`.
- **Progress persistence**: `_save_progress()` stores in Redis hash (`task_progress:{task_id}`) with TTL 30 min. `_cleanup_progress()` removes on completion.
- **Client restore**: `restoreTaskProgress()` in `events.js` fetches `/api/queue/progress/{taskId}` on SSE reconnect/page reload.
- **Stage labels** (Russian): `preparing_gpu`, `analyzing`, `analyzing_image`, `analyzing_prompt`, `generating_video`, `generating_image`, `editing_image`, `loading_reasoning_model`, `capturing_snapshot`.

### Task cancellation

- Client: cancel button (`■`) in streaming messages → POST `/api/cancel_task/{task_id}`.
- Server: `cancel_task(task_id)` sets Redis flag `task:cancel:{task_id}` with TTL. `_is_task_cancelled(task_id)` checked in every streaming loop iteration.
- SSE event: `stream_cancelled` → updates UI.

### Combined voice + image recording

- `app/static/js/chat-recording.js`: if image already attached when voice is recorded, voice stored as `attachedVoiceBlob` instead of replacing `attachedFile`. Preview shows `"image.jpg + 🎤 voice.webm"`.
- Server: `_process_transcribe_task()` creates `type: "image"` task when both `image_data` + `voice_record` present.

### DOMPurify XSS protection

- `purify.min.js` loaded in `chat.html`. All `marked.parse()` output goes through `DOMPurify.sanitize()` before DOM insertion.
- Applied in `events.js:finalizeStreamedMessage()` and `chat-messages.js:displayMessage()`.

### Lazy loading images

- All `<img>` elements created with `img.loading = 'lazy'`. `<video>` elements with `video.loading = 'lazy'`.

### Run HTML button

- `handleOpenHtmlClick()` in `chat-messages.js`: for `<code class="language-html">` blocks, creates Blob with HTML content and opens in new tab via `URL.createObjectURL()`.

### Copy message text

- `copyToClipboard(text)` in `chat-messages.js`: Clipboard API with `execCommand('copy')` fallback. Button in assistant message header.

### MTP factor in VRAM estimation

- `_estimate_model_vram()` in `app/routes/admin.py` accepts `supports_mtp: bool`. Formula: `mtp_factor = 1.15 if supports_mtp else 1.0`. MTP draft prediction layers add ~15% overhead to model weights in VRAM.

### GGUF fallback reading

- `_classify_model_fit()` and `model_vram_estimate()` in `app/routes/admin.py`: if model not in `gguf_models_cache`, reads `block_count` and `expert_count` directly from GGUF file via `gguf.GGUFReader`. Detects MTP via `{arch}.nextn_predict_layers`.

### Dead code cleanup

- Removed: `get_gguf_model_info()`, `find_gguf_file()`, `chunk_text_by_sentences()` from `app/utils.py`. `clear_camera_rooms()` from `app/cameradb.py`. `get_database_type()`, `is_postgresql()`, `close_db()` from `app/database.py`.
- Removed CSS classes: `.capabilities`, `.capability` from `admin.css` and `dark-theme.css`.
- Removed commented-out code block in `app/utils.py:1546-1549`.

### Mypy cleanup — `app/utils.py` (19 → 0 errors)

- New helper `_gguf_scalar(val)` (`app/utils.py:21-39`) normalizes gguf reader field values.
- `mypy app/utils.py` now passes with 0 errors. CI still uses `|| true` for mypy (does not block).

### `userdb.py` schema mismatch fix

- `delete_user()` used `user_id = user["id"]` (INTEGER) against TEXT columns → `user_id = login` (TEXT).
- Removed dead code `user_uploads_dir` (data/uploads/ is per-session-UUID, not per-user).

### Test infrastructure fixes

- `tests/test_backups.py`: `Babel(flask_app)` added. Version updated to `"8.9"`.
- `tests/test_resource_manager.py`: `patch("app.resource_manager.requests.X", new=mock)`.
- `app/routes/backups.py:restore_backup()`: `dirs_exist_ok=True`. Version updated to `"8.9"`.
- `tests/test_morph.py` (NEW): 14 tests for pymorphy3 morphological analysis.

### Chat video export

- `app/static/js/chat-export.js`: `saveChatAsHTML()` now collects `<video>` elements from DOM, fetches video files from `/api/files/` and converts to base64. Accept header updated to include `video/*`. Video rendered as `<video controls preload="metadata">` in exported HTML.
- `app/static/css/export.css`: Added `.video-container` and `.video-container video` styles for exported video elements.
