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
- **Queue**: `app/queue.py:RedisRequestQueue`. Two workers: fast (text, audio, RAG, camera) and slow (image gen/edit, video, document indexing). Tasks are HMAC-signed JSON.
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
- **SLM (SuperLocalMemory)**: Per-user SQLite databases at `/app/data/slm/{user}/.superlocalmemory/memory.db`. Daemon mode (`slm serve start`) keeps MemoryEngine + embedding model in memory permanently; `services/superlocalmemory/slm_http.py` proxies requests to daemon at `localhost:8765` (no subprocess per call). SLM facts are injected into prompt context for BOTH chat and reasoning models (alongside conversation history). Background import on startup via `slm_import_progress` checkpoint table. Auto-cleaned on last session deletion. Fact count visible in admin panel column.
- **`_tr()` / `self._()` format strings**: Flask-Babel 4.0.0 `gettext()` uses `%`-formatting (`string % variables`), NOT `str.format()`. Passing `{status}` kwargs directly to `gettext()` silently returns the unformatted string. Always call `gettext(key)` without kwargs, then apply `result.format(**kwargs)` manually. See `app/llamacpp_client.py:26` and `app/mixins.py:9` for the correct pattern.
- **Style**: All CSS in `app/static/css/`, JS in `app/static/js/`. No inline styles, no CDN (all assets bundled). Comments/logs in English. User-facing strings via Flask-Babel (`translations/{en,ru}/LC_MESSAGES/messages.po`). Add new keys to both `.po` files.
- **Lint config** (pyproject.toml): ruff line-length=120, select E/W/F/I/N/UP/B/SIM, ignore E501/B008/PTH. `__init__.py` per-file-ignore F401. mypy target 3.11, ignore-missing-imports, excludes tests/ and translations/.
- **Security**: Path traversal checks in `api/files/<path>`. Session ownership validated. CSRF on all forms. Secrets in `.env` only.

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

## Critical rules

1. NEVER make ANY changes to files without direct user approval. Each file change (create, edit, delete) requires explicit plan approval. Exception: only when the user explicitly said "do it" or "execute".

2. Hardcoded query filters at the Python level (without LLM) are STRICTLY FORBIDDEN. All query classification and routing MUST go through the LLM router model. Do not add pattern matching, keyword lists, or any deterministic logic to bypass the router for specific queries.

## GPU Requirement

FLAI REQUIRES an NVIDIA GPU with at least 8 GB VRAM and 16 GB system RAM. CPU-only mode is not supported — LLM inference, SD image generation, and LTX-Video all depend on CUDA. The project automatically adapts to available VRAM (8/12/16+ GB tiers), adjusting model offloading, resolution, and model selection accordingly.
