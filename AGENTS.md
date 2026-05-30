# AGENTS.md — FLAI v8.8

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
pybabel extract -F babel.cfg -o translations/messages.pot .
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

- **Entrypoint**: `app/__init__.py:create_app()` → returns Flask app. Blueprints in `app/routes/` (auth, chat, admin, queue, tts, messages, sessions, documents, backups). Modules in `modules/` (base/router, multimodal, sd_cpp, cam, rag, audio, tts, slm).
- **LLM client**: `app/llamacpp_client.py:LlamaCppClient` with two backends — `DirectLlamaBackend` (direct llama-server) or `LlamaSwapBackend` (via llama-swap proxy). Selected by `LLAMACP_BACKEND` env var.
- **Queue**: `app/queue.py:RedisRequestQueue`. Two workers with strict GPU serialization:
  - **Fast worker** — CPU-only operations: router (chat model), text, audio, RAG. The chat model (2.5 GiB) stays hot in VRAM.
  - **Slow worker** — all GPU-heavy operations: multimodal (Qwen3VL-8B), SD, LTX-Video, **reasoning** (gpt-oss-20b). Strictly sequential — only one GPU task runs at a time.
  - **VRAM guard** (`_wait_for_vram`) — before any multimodal/SD/Video call, blocks until at least 6 GiB VRAM is free (polls `nvidia-smi` every 2s, times out after 60s).
  - **Synchronous VRAM polling** (`_poll_vram`) — `_resolve_use_gpu()` and `ensure_vram_for_llm()` call `_poll_vram()` synchronously before reading `available_vram_mb` (was updated every 60s, causing stale data and OOM). After every `unload_llamacpp_model()`, a wait loop verifies VRAM is actually freed (up to 30s).
  - **VRAM guard for reasoning** (`ensure_vram_for_reasoning`) — unloads llama.cpp models and waits (up to 60s) for SD/Video to free VRAM before loading gpt-oss-20b (~10 GiB).
  - Tasks are HMAC-signed JSON.
- **DB**: PostgreSQL only via `app/database.py:get_db()` context manager (psycopg2 RealDictCursor). `DATABASE_URL` required. Tables: user_sessions, chat_sessions, messages, documents, session_visits, model_configs, user_storage, slm_import_progress, gguf_models_cache.
- **Helpers**: `app/circuit_breaker.py`, `app/resource_manager.py`, `app/llama_swap_config.py`, `app/slm_import.py` — llama-swap config auto-generated from DB at startup into `llama-swap-config/`. Background SLM import on startup.
- **Docker mounts**: `./data/` → `/app/data`, `./services/llamacpp/models/` → `/models:ro`, `/var/run/docker.sock` for GPU detection.
- **Config**: Model configs in DB (`model_configs` table). `.env` values are fallback defaults only. Admin panel at `/admin`.
- **Multimodal models**: MUST be in a subdirectory with `mmproj-*.gguf` (e.g. `Qwen3VL-8B-Instruct-Q4_K_M/`).
- **LLM backend modes**: `LLAMACP_BACKEND=llama-swap` (default in .env.example) uses llama-swap at `LLAMA_SWAP_URL=http://flai-llamaswap:8080`. `LLAMACP_BACKEND=llamacpp` (direct) uses `LLAMACPP_URL=http://flai-llamacpp:8033`.
- **VRAM** (`app/llama_swap_config.py`): All llama.cpp models share a single `llm_fast` group with `swap: true` in llama-swap. At most ONE model is loaded in VRAM at any time. TTLS: chat=600s (always hot), multimodal/reasoning/embedding=0s (unload immediately after response). SD and LTX-Video use separate GPU contexts. Three VRAM tiers (8/12/16+ GB) adjust `n_gpu_layers` and resolution caps.

  **Model lifecycle on a single consumer GPU:**
  1. **Chat (Qwen3-4B, 2.5 GiB)** — preloaded at startup and stays hot (TTL=600s). Default model for router and direct responses. Swapped out on demand when another model from the group is needed. Reloaded automatically on the next chat request.
  2. **Multimodal (Qwen3VL-8B, 5 GiB)** — loaded on demand (camera, image analysis, video param gen). TTL=0 → **unloaded immediately** after the response is sent. VRAM freed for subsequent tasks.
  3. **Reasoning (gpt-oss-20b, MXFP4, 10 GiB)** — loaded on demand for complex queries. TTL=0 → unloaded immediately after response.
  4. **Embedding (bge-m3 Q8_0, 0.5 GiB)** — TTL=0 → unloaded immediately after use.
  5. **SD / LTX-Video** — before generation, llama-swap is asked to unload all models via `POST /api/models/unload` (implemented in `resource_manager.py:unload_llamacpp_model()`). This frees ~3-4 GiB VRAM (chat). After generation, the next user request reloads chat automatically.

  **Sequence for a video generation request:**
  `router (chat) → [-VIDEO-] → multimodal loads (chat swapped out) → multimodal generates video params → multimodal unloads (TTL=0) → video pipeline loads (full VRAM available) → video generated → video pipeline unloads → next user request reloads chat`.
- **SLM (SuperLocalMemory)**: Per-user SQLite databases at `/app/data/slm/{user}/.superlocalmemory/memory.db`. Daemon mode (`slm serve start`) keeps embedding model in memory permanently; `services/superlocalmemory/slm_http.py` proxies requests to daemon at `localhost:8765` (no subprocess per call). **Per-user isolation**: recall reads directly from the user's private SQLite table (`atomic_facts`), not from the daemon's shared database. **Chat model** uses fast direct SQLite read (`ORDER BY created_at DESC`). **Reasoning model** uses full semantic search via subprocess `slm recall` (falls back to direct SQLite if no embeddings). Remember saves to both daemon (shared) and per-user DB (async subprocess). **Camera router parser**: uses text after `[-CAMERA-]` marker (room code), NOT original_query — preserves compatibility with Russian declensions (гостиная → в гостиной). **Router retry on JSON error** — `process_message()` retries once if the router returns a garbled `{"error": ...}` response. SLM facts are injected into prompt context for BOTH chat and reasoning models (alongside conversation history). **Router retry on JSON error** — `process_message()` retries once if the router returns a garbled `{"error": ...}` response. **SLM lazy availability re-check** — `_get_context_for_model()` always calls `slm.get_context()` (no `slm.available` check), the method has its own lazy re-check. **SLM dedup** — `_recall_from_user_db()` in `slm_http.py` deduplicates facts by content (score `limit × 3`, returns unique). Configurable via `SLM_RECALL_LIMIT` (default 7). Background import on startup via `slm_import_progress` checkpoint table. Auto-cleaned on last session deletion (`_cleanup_slm_if_empty()` in `db.py`). **Per-user SLM files are owned by appuser (UID 1000)** matching the web container — `start.sh` runs `chown -R appuser:appuser` on the shared volume. Fact count visible in admin panel column.
- **`_tr()` / `self._()` format strings**: Flask-Babel 4.0.0 `gettext()` uses `%`-formatting (`string % variables`), NOT `str.format()`. Passing `{status}` kwargs directly to `gettext()` silently returns the unformatted string. Always call `gettext(key)` without kwargs, then apply `result.format(**kwargs)` manually. See `app/llamacpp_client.py:26` and `app/mixins.py:9` for the correct pattern.
- **Style**: All CSS in `app/static/css/`, JS in `app/static/js/`. No inline styles, no CDN (all assets bundled). Comments/logs in English. User-facing strings via Flask-Babel (`translations/{en,ru}/LC_MESSAGES/messages.po`). Add new keys to both `.po` files.
- **UI queue indicators**: `chat-queue.js` — `fetchQueueStatus()` builds `newInfo` from server data, then preserves `processing: true` for recently tracked pending requests (race condition guard, 10s window). **Multiple ⚡ prevention**: the race guard checks if any other session is already `processing` before setting a new one; if so, the session gets `queued += 1` instead. Ensures only one ⚡ across all sessions. **Queue position display**: uses nullish coalescing (`??`) — position 0 (extra processing tasks) shows ⏳ without a number, normal queue positions show ⏳ N.
- **⚡ recovery after task chain**: `events.js` — after every `clearSessionQueue()` call, `setTimeout(fetchQueueStatus, 500)` is scheduled. This polls the server for the next queued task, restoring ⚡ when the next task moves from queue to processing.
- **Lint config** (pyproject.toml): ruff line-length=120, select E/W/F/I/N/UP/B/SIM, ignore E501/B008/PTH. `__init__.py` per-file-ignore F401. mypy target 3.11, ignore-missing-imports, excludes tests/ and translations/.
- **Security**: Path traversal checks in `api/files/<path>`. Session ownership validated. CSRF on all forms (`WTF_CSRF_TIME_LIMIT=28800`, synced with session). `session.permanent = True` at login (8h idle timeout). Secrets in `.env` only.

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

## Dependencies & External Resources
- The project must run **fully offline** after model/voice downloads.
- No external scripts, CDN links, or remotely loaded modules in production.
- All CSS must reside in `.css` files; all JavaScript in `.js` files (no inline styles/scripts).
- External dependencies (models, voices) must be documented with size, license, and download instructions.

## Cleanliness & Dead Code
- No unused files, dead code, or unused CSS/JS.
- Every import must be used; every translation key must appear in the UI.
- Run `pybabel extract` / `pybabel update` / `pybabel compile` after modifying translatable strings.
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

## Known issues (fix on sight)

- **mypy** `app/utils.py`: `Module has no attribute "parse_rtf"` — striprtf stub issue. Fix: `# type: ignore[attr-defined]`.
- **Unit test speed**: CamModule has 5×2s init retries, making test_cam.py ~10s per fixture.
- **Load tests** (`tests/load/`) excluded from pytest collection (require locust fixtures).

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
- `app/queue.py`: Added `_gpu_lock`, `_log_gpu_state_before_op()`, `_check_vram_ready()`, `_unload_llamacpp_models()`, CUDA cache flush in `_unload_video_pipeline()`, RAG in streaming path, RAG retry in reasoning task, raw chunk context from Qdrant on RAG failure
- `modules/base.py`: `process_reasoning()` accepts `rag_context` parameter
- `prompts/ru/rag.template`: Fixed — removed "answer on your own" instruction, added strict "use ONLY context" directive
- `prompts/en/rag.template`: Same fix
- `prompts/ru/base_text.template`: Added RAG category for person/document queries
- `prompts/en/base_text.template`: Same RAG category
- `prompts/ru/reasoning.template`: Added `{rag_context}` placeholder
- `prompts/en/reasoning.template`: Added `{rag_context}` placeholder
- `app/static/js/events.js`: `finalizeStreamedMessage` renders file attachments AND error messages in streaming responses
- `app/static/js/admin-models.js`: `updateMemoryEstimation()` now handles `status: "measured"` — shows measured VRAM with measurement count and ctx, and `status: "estimate"` with color-coded percentage.
- `app/db.py`: Added `file_data` to SQL SELECT, replaced `suppress(Exception)` with logging
- `translations/*.po`: Added GPU error + RAG context + VRAM estimate translations

### Monitoring commands
```bash
watch -n 1 nvidia-smi          # Real-time VRAM tracking
docker logs flai-web --tail 50 | grep GPU  # Log GPU-related events
grep "RAG\|reasoning\|router" docker/logs/flai-web.log  # Debug RAG flow
```

## Critical rules

1. NEVER make ANY changes to files without direct user approval. Each file change (create, edit, delete) requires explicit plan approval. Exception: only when the user explicitly said "do it" or "execute".

2. Hardcoded query filters at the Python level (without LLM) are STRICTLY FORBIDDEN. All query classification and routing MUST go through the LLM router model. Do not add pattern matching, keyword lists, or any deterministic logic to bypass the router for specific queries.

## GPU Requirement

FLAI REQUIRES an NVIDIA GPU with at least 8 GB VRAM and 16 GB system RAM. CPU-only mode is not supported — LLM inference, SD image generation, and LTX-Video all depend on CUDA. The project automatically adapts to available VRAM (8/12/16+ GB tiers), adjusting model offloading, resolution, and model selection accordingly.
