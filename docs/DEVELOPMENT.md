# Development

Contributor guide for FLAI: where the code lives, how to run the test suite, and the CLI tools that ship with the app. For project rules and hard constraints, read [../AGENTS.md](../AGENTS.md) — this document assumes you have.

## Code layout

| Path | What lives there |
|------|------------------|
| `app/__init__.py` | `create_app()` — the Flask entry point and blueprint registration |
| `app/routes/` | HTTP layer: auth, chat, admin, queue, messages, sessions, documents, backups, events, debug, rlm, `/v1` |
| `app/queue.py` | `RedisRequestQueue` — fast (CPU) and slow (GPU) workers, VRAM serialization, task handlers |
| `app/resource_manager.py` | Per-module KV cost, VRAM estimation, `ensure_vram_for()` |
| `app/llamacpp_client.py` | `DirectLlamaBackend` and `LlamaSwapBackend` |
| `app/database.py` | `get_db()`, schema init, `_autofit_context()` |
| `app/tasks/` | Background tasks: `dry_load.py`, `health_monitor.py` |
| `modules/` | Model subsystems: base/router, multimodal, sd_cpp, cam, rag, audio, tts, slm, search, video, crawler, rlm |
| `prompts/ru/`, `prompts/en/` | Prompt templates, `skills.txt` (single source of truth for capability lists) |
| `services/` | Non-Python services (kokoro TTS, ltxvideo wrapper) |
| `tests/` | pytest suite, `tests/load/` for Locust |
| `translations/` | Flask-Babel catalogs (`en`, `ru`) |

## Lint and type check

```bash
ruff check .
mypy app/ modules/               # CI runs with `|| true` — does not block
```

## Tests

FLAI ships tests for every key component, plus load tests for the web interface.

### Install

```bash
pip install -e ".[test]"
```

### Run

```bash
pytest                           # everything
pytest -m unit                   # only unit tests (no external deps)
pytest -m "not slow"             # skip slow tests
pytest -m "not (requires_db or requires_redis)"
pytest --cov=app --cov=modules --cov-report=html
pytest tests/test_queue.py       # a specific file
```

Each test module builds its own Flask apps (`create_app()` → worker threads, Redis, Qdrant), so state can leak between modules. The CI runs the whole suite as `pytest --forked` (`pytest-forked` in the dev extras): every module runs in its own subprocess, which isolates that state and surfaces single-module flakes instead of cross-module order effects. On a single development host the same isolation applies at a coarser level — run only the suites you touched (see AGENTS.md for the memory budget) — and the Docker image build is gated on the suite passing.

FLAI includes comprehensive testing for all key components and load testing for the web interface.

### Load testing

Load tests use [Locust](https://locust.io/) to simulate concurrent users.

```bash
# Install Locust (if not already installed)
pip install locust

# Web interface — open http://localhost:8089
locust -f tests/load/locustfile.py --host http://localhost:5000

# Headless mode — 10 users, spawn 2/sec, run 1 minute
locust -f tests/load/locustfile.py --headless -u 10 -r 2 --run-time 1m

# Using the convenience script
./tests/load/run_load_test.sh --host http://localhost:5000 --users 10 --spawn-rate 2 --run-time 1m
```

See [tests/load/README.md](../tests/load/README.md) for detailed load testing instructions.

---

### Test structure

- **Fixtures** in `tests/conftest.py`:
  - `test_app` — isolated app + temp dirs
  - `client` — Flask test client
  - `runner` — CLI runner

### Mocking external services

**External services are ALWAYS mocked**:
- Redis (`redis.from_url`)
- llama.cpp (`app.llamacpp_client.LlamaCppClient`)
- Qdrant (`modules.rag.QdrantClient`)

### Database mode

- **Mock by default** (no `DATABASE_URL`).
- **In CI** (`DATABASE_URL` set) — real PostgreSQL with `TRUNCATE` between tests via `test_app` teardown.

### Background workers

`RedisRequestQueue` threads are stopped via `stop_workers(timeout=3)` in `test_app` teardown to prevent pytest hang.

### Available markers

- `unit` — fast unit tests
- `integration` — tests requiring external services (mocked)
- `e2e` — end-to-end tests
- `slow` — long-running tests
- `requires_db` — tests requiring PostgreSQL
- `requires_redis` — tests requiring Redis

### Test examples

`tests/test_resource_manager_ltx_unload.py`

11 tests across 4 classes:
  - `Preflight` — pre-flight check logic
  - `Cache` — 30s result cache behavior
  - `SuccessCondition` — reachable success condition
  - `DockerRestart` — Docker restart on 3 consecutive timeouts

`tests/test_morph.py` **(NEW in v9.0)**

16 tests for pymorphy3 morphological analysis of camera room names.

`tests/test_slm_rules.py` **(NEW in v9.0)**

29 tests for rule-based SLM fact extraction: sentence splitting, scoring by category patterns (preferences, facts, instructions, personality), text normalization, Levenshtein similarity, fact extraction, deduplication, and explicit remember parsing.

`tests/test_slm_merge_rules.py` **(NEW in v9.0)**

16 tests for rule-based SLM fact merging: fast_cleanup (exact duplicates, fragments), edit_distance_merge (Levenshtein near-duplicates), fragment_merge (stricter substring detection), temporal_decay (auto-archive old low-confidence facts), and merge scheduling logic.

`tests/test_backups.py`

Fixed in v9.0:
`Babel(flask_app)` added to `app` fixture (was causing KeyError 'babel')
`test_restore_backup` fixed via `dirs_exist_ok=True` in `app/routes/backups.py:restore_backup()` (was causing `shutil.copytree FileExistsError` on `data/slm`)

`tests/test_api_docs.py` **(NEW in v12.3)**

5 tests for the interactive API reference: Swagger UI served without any CDN references (asset `src`/`href` on `script`/`link`/`img` tags only — plain `<a href>` attribution links load nothing), a valid OpenAPI 3.0.3 document at `/v1/openapi.json`, global Bearer authentication declared, **route drift guard** (every registered `/v1` route must have a spec entry and vice versa, doc routes `/v1/docs*` + `/v1/openapi.json` excluded), and key paths presence. The drift guard keeps `docs/openapi-v1.yaml` from falling behind the implementation.

`tests/test_api_inventory.py` **(NEW in v12.3)**

Contract guard enumerating all 23 expected `/v1` endpoints with their method sets; fails the build on a missing or unexpected route (documentation routes `/v1/docs`, `/v1/docs/oauth2-redirect.html`, `/v1/openapi.json` are allowlisted separately).

Public `/v1` API test files (all added in v12.3): `test_api_v1_auth.py` (Bearer auth, 8 tests), `test_api_v1_chat.py` (chat sync/SSE/session continuity, 28 tests), `test_api_v1_embeddings.py` (12 tests), `test_api_v1_media.py` (image/video/audio generation, 77 tests), `test_api_v1_ratelimit.py` (9 tests), `test_api_chat_async.py` (async chat + poll, 16 tests), `test_api_tasks.py` (owner-scoped task list/status/cancel/content, 27 tests), `test_api_documents.py` (12 tests), `test_api_rlm.py` (8 tests), `test_api_sessions.py` (8 tests), `test_api_limits.py` (19 tests: wait-slot cap, CORS), `test_api_bridge.py` (68 tests: session resolution, enqueue, wait, stream, requeue ownership), `test_api_tokens.py` (API key management, 11 tests).

**Web Crawler (Crawl4AI, v12.4) test files (all NEW in v12.4):** `test_crawler_guard.py` (8 tests: SSRF ranges, credentials, port), `test_crawler_config.py` (3 tests: allow-list defaults, env parsing), `test_crawler_module.py` (15 tests: availability, /md contract, client-side BFS scope, caps, deadline, anti-bot `SiteBlockedError`), `test_read_page_tool.py` (7 tests: tool schema, gating, localized errors), `test_router_crawl.py` (2 tests: `[-CRAWL-]` marker parsing, search marker untouched), `test_crawl_task.py` (42 tests: dedicated executor, no GPU coupling outside the indexing lock, domain replacement, quota, context budget, anti-bot fallback to web search), `test_crawl_ui.py` (15 tests: compose service placement + no published ports, `docker compose config`, stage labels in ru/en catalogs vs chat.html msgids, counter reads `data.pages`, deploy flag init).

### Known test issues
  - **Unit test speed:** `CamModule` has 5×2s init retries, making `test_cam.py` ~10s per fixture. Not blocking, but slow.
  - **Load tests** (`tests/load/`) excluded from pytest collection — require locust fixtures. Run separately: `locust -f tests/load/locustfile.py --host http://localhost:5000` or `locust -f tests/load/locustfile_public.py --host http://localhost:5000` for public endpoints.

### Test infrastructure fixes (v9.0)
  - `tests/test_backups.py`: `Babel(flask_app)` added.
  - `tests/test_resource_manager.py`: `patch("app.resource_manager.requests.X", new=mock)`.
  - `app/routes/backups.py:restore_backup()`: `dirs_exist_ok=True`.
  - `tests/test_morph.py` **(NEW):** 16 tests for pymorphy3 morphological analysis.

## Configuration for contributors

When adding or changing environment variables in `app/config.py`, both `.env` and `.env.example` MUST be updated. `.env` contains real values; `.env.example` has placeholders and comments. Section order must match.

When adding or changing environment variables in `app/config.py`, both `.env` and `.env.example` MUST be updated. `.env` contains real values, `.env.example` has placeholders and comments. Section order must match, and secrets from `.env` must never reach `.env.example`. The full variable reference lives in [CONFIGURATION.md](CONFIGURATION.md).

## CLI tools

```bash
docker exec flai-web flask admin-password <password>
docker exec flai-web flask cleanup-uploads
docker exec flai-web flask migrate-messages-format [--dry-run]
docker exec flai-web flask backfill-attachment-paths [--dry-run]
docker exec flai-web flask import-history-to-slm [--force] [user_id]
```

`backfill-attachment-paths` migrates legacy chat messages whose attachment payloads still live as base64 inside the content JSON: it saves each part to disk via `save_uploaded_file` (the primary part reuses the row-level `file_path`), replaces the payload with the saved relative path and clears the column `file_data` only where a `file_path` exists. It commits per row, is idempotent (a re-run updates 0 messages) and `--dry-run` reports without writing anything; take a database backup first.

Long-term memory maintenance talks to the SLM container directly:

```bash
# All users
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"

# Single user
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({'profile':'valery'}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"
```

## Dev server

```bash
python wsgi.py                  # 0.0.0.0:5000, debug=True
gunicorn -c gunicorn_config.py wsgi:app   # production: 1 worker, 900s timeout
```

## Documentation

Readme-level documentation is user-facing and translated. Code-level documentation is English-only. See [../AGENTS.md](../AGENTS.md) rule 6.
