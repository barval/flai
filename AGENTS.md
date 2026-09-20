# AGENTS.md — FLAI v12.0

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
  --profile with-image-gen --profile with-voice-piper --profile with-rag \
  --profile with-video --profile with-slm --profile with-search up -d

# Load test
locust -f tests/load/locustfile.py --host http://localhost:5000
```

## Architecture Overview
FLAI is a self-hosted multimodal AI assistant running on a **single consumer NVIDIA GPU (8/12/16+ GB)**. It orchestrates multiple models (multimodal, reasoning, embedding, SD, LTX-Video) through a strict GPU queue with VRAM-aware scheduling.
  - **Entrypoint:** `app/__init__.py:create_app()` (Flask)
  - **Blueprints:** `app/routes/` — auth, chat, admin, queue, tts, messages, sessions, documents, backups, events, debug, rlm
  - **Modules:** `modules/` — base/router, multimodal, sd_cpp, cam, rag, audio, tts, slm, search, video, rlm
  - **Task progress stages:** `task_progress` SSE events drive localized status labels («Анализирую запрос...», «Ищу в документах...», «Ищу в интернете...», «Загружаю модель рассуждений...», «Обдумываю ответ...»). `reasoning_thinking` is emitted via `status_callback` — both LLM backends fire it right after `POST /v1/chat/completions` returns (model loaded, generation starts), because the entire `reasoning_content` phase streams no content tokens. Labels live in `chat.html` TRANSLATIONS (`stage_*` msgids), stage→key map in `events.js:STAGE_LABEL_KEYS` (+ `STAGE_COUNTER_KEYS` for `results`/`chunks` counts); each indicator ticks elapsed seconds and one element per session is kept (phase chains cross task ids).
  - **Background tasks:** `app/tasks/` — `dry_load.py` (dry-load after admin save; also scheduled for context-only changes, with a context-aware rollback that restores `context_length`, not the fallback model), `health_monitor.py` (crash-loop watchdog; **skips its whole tick while `ResourceManager.is_gpu_busy()` is active** — SD, video, or an `ensure_vram_for` unload+wait cycle — because a health check spawned mid-wait respawns the model being unloaded and starves the VRAM wait). Fact extraction runs as background thread (CPU-only, rule-based via `app/slm_rules.py`). Fact merge runs on background queue (CPU-only, no LLM). Both excluded from queue status display and user counter.
  - **LLM client:** `app/llamacpp_client.py` with `DirectLlamaBackend` and `LlamaSwapBackend`. Both `call()` and `chat()` accept `temperature` parameter. Router classification uses hardcoded `temperature=0.1`. `_translate_llama_swap_error()` translates llama-swap errors to user language. `_strip_generic_reasoning()` threshold `>=2` markers (synced with JS client), overbroad Russian patterns removed to prevent false positives. **Repetition-loop detector:** streaming content goes through `_LoopGuard` (hold-back 1500 chars, `_repetition_cutoff` detects ≥3 equal-gap repetitions of the last-200-char block); both backends route in-loop content AND end-of-stream remainders (`_stream_buffer` + `_reasoning_stream` + `_loop_guard` flushes, in that order) through the guard so short answers are never dropped or reordered. Safety net `strip_repetition_loop()` in `app/queue.py:_process_reasoning_request()` cuts loops missed in streaming.
  - **Queue:** `app/queue.py:RedisRequestQueue` with **fast worker (CPU) and slow worker (GPU)**. Cancel support for all task types: image gen/edit (pre/post checks), video gen (background checker thread + container restart), streaming tasks (Redis flag). Reasoning tasks retry once when the model returns empty output (thinking-only after `_strip_thinking_tags()`) — `_process_reasoning_request()` loops the generation up to 2 attempts; the retry is fast because the first attempt already loaded the model just-in-time. No retry on task cancellation or on genuine LLM error strings.
  - **RLM deep analysis (v12.0):** an explicit "Deep analysis" toggle in chat routes a user question + selected documents to an `rlm_analysis` task type (slow worker, GPU). Documents are selected by clicking them in the documents panel (`rlmSelectedDocs` in `chat-documents.js` syncs the hidden `#rlm-docs` multi-select); an attached image is described by the multimodal model (`describe_image_for_rlm()` in `modules/multimodal.py`, prompt `prompts/{ru,en}/rlm_image.template`) and the description joins the corpus as a `«Изображение (file_name)»` document — if the toggle cannot start (no documents and no image, or an image without a question) it is auto-unchecked and the request falls through to the normal send flow. `_process_rlm_task()` in `app/queue.py` orchestrates a reasoning actor loop (RLM_MAX_STEPS=18 as hard ceiling — the per-host allowance comes from the resource ladder in `modules/rlm.py:_resource_step_budget()` (24 GB+→18, 16 GB→12, 12 GB→10, 8 GB→8, CPU/<8 GB→6); the context window never cuts steps — `_obs_trunc_for_context()` compresses observations instead, down to an 800-char floor; RLM_TASK_TIMEOUT=0 auto-derives the wall-clock deadline from the step budget and platform (`_auto_task_timeout()`: GPU 120+90×steps, CPU 240+300×steps; `-1` disables; on expiry the run ends with the localized «task exceeded the time limit» error and the partial trace is saved), RLM_CODE_TIMEOUT=15 s, RLM_OBS_TRUNC=4000) over the corpus = selected documents (not RAG) + image description + web results (SearXNG, RLM_WEB_MAX_FETCHES=5); each step can call the sandboxed `python` tool, an `llm()` sub-call (RLM_SUB_MAX_TOKENS=1024), or `web_fetch(query)`, and `final(answer)` to exit. **OOM protection:** the total corpus size is capped at `RLM_MAX_CORPUS_CHARS` (50_000_000 default) — an oversized corpus is rejected with a localized error before any GPU/VRAM work (never loaded into the forked sandbox). **The whole analysis is ONE GPU task:** it JIT-loads the resident reasoning model and holds the GPU lock for its full duration (`ResourceManager.mark_rlm_busy()` extends `is_gpu_busy()` so the v11.5 watchdog skips); the per-step reload cost (reasoning `ttl=1s`) is accepted, and the image description phase runs before it under `ensure_vram_for("multimodal")`. **Sandbox:** `app/rlm_sandbox.py` — isolated long-lived fork child; AST whitelist (`validate_code` with `FORBIDDEN_NODES`/`FORBIDDEN_NAMES`/`SAFE_BUILTINS`) + rlimits; no network/FS; `llm()`/`web_fetch()` are IPC callbacks to the parent (`SandboxBroker`). Per-step trace → Redis `rlm_trace:<task_id>`; stages `stage_rlm_*` with a collapsible "Deep analysis (N steps)" summary (the full trace is stored but not rendered yet); sub-model introspection results are NOT persisted to the DB. Env vars: `RLM_ENABLED`/`RLM_ACTOR_MODEL`/`RLM_MAX_STEPS`/`RLM_TASK_TIMEOUT`/`RLM_CODE_TIMEOUT`/`RLM_OBS_TRUNC`/`RLM_SUB_MAX_TOKENS`/`RLM_WEB_MAX_FETCHES`/`RLM_MAX_CORPUS_CHARS` (defaults in `app/config.py`).
  - **Reasoning thinking budget (CLI flag):** llama-server build 10603 ignores the per-request `reasoning_budget` field, so `app/llama_swap_config.py` passes the budget via `--reasoning-budget max(1024, ctx_size*0.4)` (e.g. 9830 for the 24576 reasoning window) in the reasoning model's server command; the window itself is auto-fit at deployment (`_autofit_context()` — 24576 on 16 GB GPU, 16384 on 8 GB, 8192 CPU). Without it the model could burn the whole context on `reasoning_content` and never emit an answer («⚠️ Не удалось получить ответ от модели рассуждений»).
  - **VRAM management:** `app/resource_manager.py` — per-module KV cache cost (`KV_PER_TOKEN_MB`) and the multimodal mmproj (vision encoder) size are included in `compute_llamacpp_config()`, `get_vram_needed_mb()`, and the admin estimate chain (`_estimate_model_vram()` → `_classify_model_fit()`). GGUF metadata cache lookups strip the `.gguf` suffix (cache stores names without it). **Context auto-fit (v11.4):** the GPU seed calls `_autofit_context()` in `app/database.py` to pick each model's `context_length` from the GGUF metadata (read from disk) + measured RAM/VRAM; context migrations are VRAM-aware (they only raise a window when the auto-fit target is larger than the current value).
  - **Database:** PostgreSQL only via `app/database.py:get_db()`
  - **External services:** llama-swap, Qdrant, SearXNG, Piper (TTS, default) / Kokoro (TTS, selectable backend), Whisper (STT), SuperLocalMemory (SLM)
  - **LLM backend:** `LLAMACP_BACKEND=llama-swap` (default) or `llamacpp` (direct)
  - **Skills master copy:** `prompts/{ru,en}/skills.txt` — single source of truth for all capabilities lists. `format_prompt()` auto-injects `{skills_section}`.
  - **Response styles:** `STYLE_INSTRUCTIONS` in `modules/base.py` — single source of truth for 5 styles (neutral, academic, professional, friendly, funny). Imported by `rag.py` and `multimodal.py`. Style is injected into all prompts via `{response_style}` placeholder.
  - **Web search content extraction:** `_fetch_page_content()` in `modules/search.py` downloads pages with short/poor SearXNG snippets (<300 chars) via HTTP in parallel (`ThreadPoolExecutor`, max 3 workers) and extracts readable text using `trafilatura`. Instructions softened from «USE ONLY THIS DATA» to «use as primary source» in both `modules/base.py` and `prompts/{ru,en}/reasoning.template`. Configurable per-page timeout (8s default via `requests.get`). Depends on `trafilatura>=2.0.0`. (Explicit `categories=general,news` was removed — caused DuckDuckGo rate limiting on SearXNG.) **Date normalization:** `enhance_query_with_date()` resolves relative date words («позавчера/вчера/сегодня», English equivalents) to absolute dates in the user's timezone before sending the query to SearXNG — engines return dated articles instead of section landing pages; queries without relative date words are untouched (normalization, not routing).
  - **SearXNG engine roster & retry:** `searxng/settings.yml` enables google news, bing news, yahoo news, yahoo, bing, mojeek, marginalia, presearch, qwant, yandex, swisscows news on top of default google cse/duckduckgo. `_process_search_task()` in `app/queue.py` retries once when a search returns 0 results; if still empty, the user gets a soft localized «Search services are temporarily unavailable. Please try again in a few minutes.» notice (msgid in both `.po` files) instead of the old «No web search results found» hard error. **Decimal-amount normalization (v11.5):** engines answer a query with a fractional amount (`£3293.31`) with a converter widget and zero organic links, so `simplify_search_query()` in `modules/search.py` strips decimal amounts for the retry only. `_process_search_task()` takes a separate `reasoning_query` (the user's original `message_text` passed from `_route_text_action()`) so the reasoning model keeps the amount while engines get a plain rate lookup; category 6 in `prompts/{ru,en}/base_text.template` tells the router to search for the rate/price, not restate the arithmetic. The `0 results` diagnostic reports `engines without a response` (some engines answer with zero, they did not fail). Conversions at a live rate are forced to category 6 by a SPECIAL CONVERSION RULE in `prompts/{ru,en}/base_text.template` (mirrored in the router system hint in `modules/base.py:process_message()`): converting an amount at a current rate/price is ALWAYS a web search, never offline `[-REASONING-]`, and the search query is the rate/price lookup without the amount.
  - **Reasoning with fresh web data (`[-REASONING-WEB-]`, v11.5):** the router's 9th category (category 9 in `prompts/{ru,en}/base_text.template`; marker in `modules/base.py:_parse_router_response`, `needs_reasoning` covers both `reasoning` and `reasoning_web`) routes a complex query that also needs current internet data through the **existing** `_process_search_task()` (SearXNG on the fast worker, CPU-only) and then re-queues the reasoning model with the collected `rag_context` (`rag_source="web_search"`), preserving the session history (unlike the offline `[-REASONING-]` path, which uses `skip_rag=True`). `_get_model_for_task()` maps `reasoning_web` to `none`, so the fast worker takes no GPU lock for the search phase. Graceful degradation: `_process_search_task(graceful=True)` returns `{"status": "no_search"}` without persisting an error message, and `_route_text_action()` falls back to plain reasoning. Distinguishing rule: `[-REASONING-]` = thinking without external data (code/writing/derivation); `[-REASONING-WEB-]` = analysis/overview over fresh facts; topics that only need a lookup stay in categories 5/6.
  - **Context budget:** `_get_context_for_model()` fetches SLM facts first, measures real token cost, then fills remaining budget with conversation history. No hardcoded reserves — actual sizes used throughout. Search content is truncated to 2 000 chars per result and a dynamic total computed from the reasoning model's `context_length` via `get_search_context_limit()` (~30% of effective budget, ~11 K chars for 16 K context). When budget is still exceeded, RAG+SLM is returned without history (never dropped). **Rolling session summaries (v11.3):** if the budget trims old messages, the trimmed prefix is folded into a compact per-session summary via the multimodal model (`prompts/{ru,en}/summarize.template`, stored in `chat_sessions.summary`/`summary_upto_id`) and injected before history — the conversation thread survives trimming. **Token calibration (v11.3):** the client records `usage.prompt_tokens` per model into a char/token calibrator in `app/utils.py`; `estimate_tokens()` prefers the calibrated median over static coefficients, converging the estimate onto the real tokenizer and letting context margins be tighter.
  - **Chat auto-scroll:** `_isLoadingMessages` flag in `chat-messages.js` prevents N competing async scroll callbacks. `isNearBottom()` threshold=200px. `overflow-anchor: none` for chat container.
  - **TTS markdown cleanup:** `clean_markdown_for_tts()` in `app/utils.py` strips markdown formatting before TTS synthesis (Piper or Kokoro). Handles orphaned `**` fragments from sentence-split at `.` inside URLs. Called in `modules/tts.py:synthesize()`.
  - **Kokoro cold-start warmup:** `services/kokoro/app.py:_warmup_ru()` runs one background ru synthesis at startup (`KOKORO_WARMUP_G2P=1`), so the first ru phrase takes ~2 s instead of ~56 s. RUAccent G2P worker is unloaded after `KOKORO_G2P_IDLE_TIMEOUT` (default 300, `0` = never). Client timeout `KOKORO_TIMEOUT=120`. The frontend sends `gender` explicitly in every synthesize payload (stale session cookie after `/set-voice-gender` must not change the voice).

**Full architecture details** → `docs/ARCHITECTURE.md`

---

## 🚨 CRITICAL RULES — NEVER VIOLATE

# 1. GPU Queue — Strict Serialization
  - **Tasks run strictly sequentially on GPU.** Both fast and slow workers MUST acquire `_gpu_lock` for any GPU task (chat, multimodal, embedding, reasoning).
  - **NEVER allow two GPU tasks to run concurrently.**
  - **VRAM is unconditionally cleaned between every GPU task.** After each task: unload all llama.cpp models, unload video pipeline, flush CUDA cache. No "predictive" logic. The `ensure_vram_for()` wait loop is self-healing (v11.5): a model reappearing in llama-swap `/running` during the wait was respawned externally and is re-unloaded (idempotent POST) on every poll instead of timing out.
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
  - For code paths that bypass it (e.g., string errors from `call_llamacpp()`), use `_is_llm_error_string()` (in `app/queue.py:755`) check and route through `_build_error_response()`.
  - Raw `str(e)` must NEVER be returned to the user.

# 4. Git — No Autonomous Commits
  - **NEVER make commits unless explicitly asked.**
  - Always ask before using `git add`, `git commit`, `git push`, `git tag`, or `git revert`.
  - Reverting commits without user permission is also forbidden.
  - **NEVER make ANY changes to files without direct user approval.** Each file change (create, edit, delete) requires explicit plan approval. Exception: only when the user explicitly said "do it" or "execute".

# 5. Documentation Language
  - **AGENTS.md, `CHANGELOG.md`, `README.md`, and all `docs/*.md` must be written in English only.** No Cyrillic allowed, including historical entries.
  - All code comments and log messages must be in English.
  - All user-facing messages (UI, notifications, errors) must use the selected user language (i18n).
  - The only exceptions: `deploy-ru.sh`, `README-ru.md`, and `LICENSE-ru` may contain Russian.

  ---

# Code Style & Quality
  - **Lint:** `ruff check .` (line-length=120, select E/W/F/I/N/UP/B/SIM, ignore E501/B008/PTH)
  - **Types:** `mypy app/ modules/` (target 3.11, ignore-missing-imports)
  - **No typos, syntax errors, or unreachable code.**
  - **No unused files, dead code, or unused CSS/JS.**
  - Every import must be used; every translation key must appear in the UI.
  - Remove any leftover debug prints, commented-out blocks, or obsolete TODOs.
  - All CSS in `app/static/css/`, JS in `app/static/js/`. No inline styles, no CDN.
  - **Docker dev workflow:** `./app`, `./modules`, and `./prompts` are bind-mounted as directories in `docker-compose.gpu.yml`. All changes to Python/JS/CSS/prompt files take effect immediately on container restart (no rebuild). `PYTHONDONTWRITEBYTECODE=1` is set to prevent `__pycache__` on host.
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
  - **HTML preview (`/api/html-preview/<message_id>`):** the chat ▶ button opens the message's ```html block server-side (not via a blob: URL — blob pages inherit the chat's strict CSP and CDN imports render a blank screen). The endpoint validates login + session ownership and sets its own relaxed CSP (`X-Own-CSP: 1` marker skips the global `after_request` CSP override); CDN origins are whitelisted only on that response.

# .env Synchronization Rule
  - When adding, removing, or changing environment variables in `app/config.py`, **both** `.env` and `.env.example` MUST be updated.
  - `.env` contains real values (secrets, URLs, enabled features).
  - `.env.example` contains placeholder values (`your_secret_key_here`) and comments for optional/deactivated services.
  - Do NOT commit secrets from `.env` into `.env.example`.
  - Section order and structure must match between the two files.

# Hardware Requirements
FLAI has two deployment modes: **GPU mode** (NVIDIA, recommended, 8/12/16+ GB VRAM tiers, 16 GB+ RAM matching the tier table in README) and **CPU-only mode** (no GPU, 16 GB+ RAM, 12+ cores recommended; ~2.5–25× slower depending on the task). v11.4 lightweight CPU stack: gpt-oss-20b-mxfp4 (reasoning, native MXFP4) + Qwen3VL-4B (multimodal, ~2.5 GB + mmproj), context 8192. The GPU seed config auto-fits context windows to the hardware via `_autofit_context()` in `app/database.py` (reads GGUF metadata from disk: `arch_max_ctx`, file size, `expert_count`, `supports_mtp` + measured RAM/VRAM; tier goals 32768/32768 (24 GB), 32768/24576 (16 GB), 16384/16384 (8–12 GB), falling back to 32768/24576 when VRAM is unknown). The project automatically adapts to available VRAM (8/12/16+ GB tiers) and, on CPU, auto-degrades video to a format fitting both available RAM and a wall-clock budget (`LTX_VIDEO_CPU_TIME_BUDGET_S`, default 85% of `LTX_VIDEO_TIMEOUT`; format = largest of 768×512×240 → 384×256×120 @ 12 fps → 256×192×57 @ 6 fps whose estimated time fits).

# If You Encounter an Unknown Error
  1. **DO NOT try to fix it blindly or change the architecture.**
  2. Check logs: `docker logs flai-web --tail 100`.
  3. For GPU/VRAM issues: run `nvidia-smi` and check llama-swap status.
  4. Check `CHANGELOG.md` — the issue may be a known regression.
  5. **Stop and ask the user**, providing the error log and your hypothesis.

# Known Issues (fix on sight)
  - **Unit test speed:** `CamModule` has 5×2s init retries, making `test_cam.py` ~10s per fixture.
  - **Load tests** (`tests/load/`) excluded from pytest collection (require locust fixtures).
  - All other historical issues were fixed in v10.0. See `CHANGELOG.md` for details.