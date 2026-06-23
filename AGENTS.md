# AGENTS.md — FLAI v9.0

> **Read this file first.** It contains the project's constitution: commands, critical rules, and hard constraints.
> For deep technical details, see the `docs/` directory.

## 📚 Documentation Map

| Topic | File | When to read |
|-------|------|--------------|
| Full architecture, modules, data flow | `docs/ARCHITECTURE.md` | When modifying core logic, queue, or modules |
| VRAM management, GPU queue, model protection | `docs/VRAM_MANAGEMENT.md` | When touching `resource_manager.py`, `queue.py`, video/multimodal |
| Localization, `_tr()`, Flask-Babel, README translations | `docs/LOCALIZATION.md` | When adding user-facing strings or updating READMEs |
| Testing, fixtures, markers, mocking | `docs/TESTING.md` | When writing or running tests |
| Release process, version bumps, README updates | `docs/RELEASE_GUIDE.md` | When preparing a new version |
| Historical changes, bug fixes, migration notes | `CHANGELOG.md` | When debugging or understanding why something works this way |

## Commands (exact)

```bash
# Install
pip install -e ".[dev]"          # full dev deps (ruff, mypy, types)
pip install -e ".[test]"         # just pytest deps

# Lint & type check
ruff check .
mypy app/ modules/               # CI runs with `|| true` — does not block

# Test
pytest                           # all tests
pytest -m unit                   # markers: unit, integration, e2e, slow, requires_db, requires_redis
pytest -m "not slow"
pytest --cov=app --cov=modules --cov-report=html

# Translations
pybabel extract -F babel.cfg -k _tr -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations
pybabel compile -d translations  # after editing .po files

# Admin tasks (in container)
docker exec flai-web flask admin-password <pass>
docker exec flai-web flask cleanup-uploads
docker exec flai-web flask migrate-messages-format [--dry-run]
docker exec flai-web flask import-history-to-slm [--force] [user_id]

# SLM cleanup (in container)
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"  # all users
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({'profile':'valery'}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"  # single user

# Dev server (0.0.0.0:5000, debug=True)
python wsgi.py

# Production (gunicorn 1 worker, 900s timeout)
gunicorn -c gunicorn_config.py wsgi:app

# Docker compose (all profiles)
docker compose -f docker-compose.gpu.yml \
  --profile with-image-gen --profile with-voice --profile with-rag \
  --profile with-video --profile with-slm --profile with-search up -d

# Load test
locust -f tests/load/locustfile.py --host http://localhost:5000
```

## Architecture Overview
FLAI is a self-hosted multimodal AI assistant running on a **single consumer NVIDIA GPU (8/12/16+ GB)**. It orchestrates multiple models (chat, reasoning, multimodal, embedding, SD, LTX-Video) through a strict GPU queue with VRAM-aware scheduling.
  - **Entrypoint:** `app/__init__.py:create_app()` (Flask)
  - **Blueprints:** `app/routes/` — auth, chat, admin, queue, tts, messages, sessions, documents, backups, events, debug
  - **Modules:** `modules/` — base/router, multimodal, sd_cpp, cam, rag, audio, tts, slm, search, video
  - **Background tasks:** `app/tasks/` — `dry_load.py`, `health_monitor.py`. Fact extraction runs as background thread (CPU-only, rule-based via `app/slm_rules.py`). Fact merge runs on background queue (CPU-only, no LLM). Both excluded from queue status display and user counter.
  - **LLM client:** `app/llamacpp_client.py` with `DirectLlamaBackend` and `LlamaSwapBackend`. Both `call()` and `chat()` accept `temperature` parameter. Router classification uses hardcoded `temperature=0.1`. `_translate_llama_swap_error()` translates llama-swap errors to user language. `_strip_generic_reasoning()` threshold `>=2` markers (synced with JS client), overbroad Russian patterns removed to prevent false positives.
  - **Queue:** `app/queue.py:RedisRequestQueue` with **fast worker (CPU) and slow worker (GPU)**. Cancel support for all task types: image gen/edit (pre/post checks), video gen (background checker thread + container restart), streaming tasks (Redis flag).
  - **VRAM management:** `app/resource_manager.py`
  - **Database:** PostgreSQL only via `app/database.py:get_db()`
  - **External services:** llama-swap, Qdrant, SearXNG, Piper (TTS), Whisper (STT), SuperLocalMemory (SLM)
  - **LLM backend:** `LLAMACP_BACKEND=llama-swap` (default) or `llamacpp` (direct)
  - **Skills master copy:** `prompts/{ru,en}/skills.txt` — single source of truth for all capabilities lists. `format_prompt()` auto-injects `{skills_section}`.
  - **Response styles:** `STYLE_INSTRUCTIONS` in `modules/base.py` — single source of truth for 5 styles (neutral, academic, professional, friendly, funny). Imported by `rag.py` and `multimodal.py`. Style is injected into all prompts via `{response_style}` placeholder.
  - **Context budget:** `_get_context_for_model()` fetches SLM facts first, measures real token cost, then fills remaining budget with conversation history. No hardcoded reserves — actual sizes used throughout.
  - **Chat auto-scroll:** `_isLoadingMessages` flag in `chat-messages.js` prevents N competing async scroll callbacks. `isNearBottom()` threshold=200px. `overflow-anchor: none` for chat container.
  - **TTS markdown cleanup:** `clean_markdown_for_tts()` in `app/utils.py` strips markdown formatting before Piper TTS synthesis. Handles orphaned `**` fragments from sentence-split at `.` inside URLs. Called in `modules/tts.py:synthesize()`.

**Full architecture details** → `docs/ARCHITECTURE.md`

---

## 🚨 CRITICAL RULES — NEVER VIOLATE

# 1. GPU Queue — Strict Serialization
  - **Tasks run strictly sequentially on GPU.** Both fast and slow workers MUST acquire `_gpu_lock` for any GPU task (chat, multimodal, embedding, reasoning).
  - **NEVER allow two GPU tasks to run concurrently.**
  - **VRAM is unconditionally cleaned between every GPU task.** After each task: unload all llama.cpp models, unload video pipeline, flush CUDA cache. No "predictive" logic.
  - **Degradation happens BEFORE model load, not after failure.** `compute_llamacpp_config()` iteratively reduces `n_gpu_layers` until the model fits. If it doesn't fit even with 0 layers → return error, don't crash with OOM.
  - **RAG generation NEVER runs on the fast worker.** Fast worker does ONLY `rag.search()` (embedding + Qdrant). Answer generation via reasoning model happens EXCLUSIVELY on the slow worker via `_requeue_reasoning_task()`.

**Full VRAM rules** → `docs/VRAM_MANAGEMENT.md`

# 2. No Hardcoded Routing
  - **Hardcoded query filters at the Python level (without LLM) are STRICTLY FORBIDDEN.**
  - All query classification and routing MUST go through the LLM router model.
  - Do NOT add pattern matching, keyword lists, or any deterministic logic to bypass the router for specific queries.

# 3. Error Messages
  - **All error messages displayed to users MUST start with "⚠️ ".**
  - `_build_error_response()` adds this prefix automatically.
  - For code paths that bypass it (e.g., string errors from `call_llamacpp()`), use `_is_llm_error_string()` (in `app/queue.py:782`) check and route through `_build_error_response()`.
  - Raw `str(e)` must NEVER be returned to the user.

# 4. Git — No Autonomous Commits
  - **NEVER make commits unless explicitly asked.**
  - Always ask before using `git add`, `git commit`, `git push`, `git tag`, or `git revert`.
  - Reverting commits without user permission is also forbidden.
  - **NEVER make ANY changes to files without direct user approval.** Each file change (create, edit, delete) requires explicit plan approval. Exception: only when the user explicitly said "do it" or "execute".

# 5. Documentation Language
  - **AGENTS.md and all `docs/*.md` must be written in English only.**
  - All code comments and log messages must be in English.
  - All user-facing messages (UI, notifications, errors) must use the selected user language (i18n).
  - The only exception: `deploy-ru.sh` and `README-ru.md` may contain Russian.

  ---

# Code Style & Quality
  - **Lint:** `ruff check .` (line-length=120, select E/W/F/I/N/UP/B/SIM, ignore E501/B008/PTH)
  - **Types:** `mypy app/ modules/` (target 3.11, ignore-missing-imports)
  - **No typos, syntax errors, or unreachable code.**
  - **No unused files, dead code, or unused CSS/JS.**
  - Every import must be used; every translation key must appear in the UI.
  - Remove any leftover debug prints, commented-out blocks, or obsolete TODOs.
  - All CSS in `app/static/css/`, JS in `app/static/js/`. No inline styles, no CDN.
  - Always write clean, self-documenting code; add comments only when necessary.

# Localization (i18n)
  - Use Flask-Babel 4.0.0. `_tr()` **uses `%`-formatting, NOT** `str.format()`.
  - Always call `gettext(key)` without kwargs, then apply `result.format(**kwargs)` manually.
  - Every user-facing string MUST be wrapped in `_()` / `self._()` / `gettext()`.
  - Always keep `translations/{en,ru}/LC_MESSAGES/messages.po` up-to-date.
  - When adding/modifying error messages, verify keys exist in BOTH `.po` files.

**Full localization rules** → `docs/LOCALIZATION.md`

# Dependencies & External Resources
  - The project must run fully offline after model/voice downloads.
  - No external scripts, CDN links, or remotely loaded modules in production.
  - All Python dependencies must have open-source licenses (MIT, BSD, Apache 2.0, MPL).
  - **Proprietary or copyleft (GPL/AGPL) dependencies are prohibited.** Verify license before adding.
  - External dependencies (models, voices) must be documented with size, license, and download instructions.

# Security
  - Path traversal checks in `api/files/<path>`.
  - Session ownership validated.
  - CSRF on all forms (`WTF_CSRF_TIME_LIMIT=28800`).
  - `session.permanent = True` at login (8h idle timeout).
  - Secrets in `.env` only.
  - All `marked.parse()` output goes through `DOMPurify.sanitize()` before DOM insertion.

# .env Synchronization Rule
  - When adding, removing, or changing environment variables in `app/config.py`, **both** `.env` and `.env.example` MUST be updated.
  - `.env` contains real values (secrets, URLs, enabled features).
  - `.env.example` contains placeholder values (`your_secret_key_here`) and comments for optional/deactivated services.
  - Do NOT commit secrets from `.env` into `.env.example`.
  - Section order and structure must match between the two files.

# Hardware Requirements
FLAI REQUIRES an **NVIDIA GPU with at least 8 GB VRAM and 16 GB system RAM.** CPU-only mode is not supported. The project automatically adapts to available VRAM (8/12/16+ GB tiers).

# If You Encounter an Unknown Error
  1. **DO NOT try to fix it blindly or change the architecture.**
  2. Check logs: `docker logs flai-web --tail 100`.
  3. For GPU/VRAM issues: run `nvidia-smi` and check llama-swap status.
  4. Check `CHANGELOG.md` — the issue may be a known regression.
  5. **Stop and ask the user**, providing the error log and your hypothesis.

# Known Issues (fix on sight)
  - **Unit test speed:** `CamModule` has 5×2s init retries, making `test_cam.py` ~10s per fixture.
  - **Load tests** (`tests/load/`) excluded from pytest collection (require locust fixtures).
  - All other historical issues were fixed in v9.0. See `CHANGELOG.md` for details.