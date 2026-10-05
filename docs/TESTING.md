# Testing — FLAI v12.4

This document describes the testing infrastructure, fixtures, mocking strategy, and known test issues. Read it when writing or running tests.

> **v10.0 note:** All test fixtures migrated from `chat` → `multimodal` module type across 12+ test files. `test_validators.py` updated `MODULE_TYPES` assertions to `{"multimodal", "reasoning", "embedding"}` (chat removed). `test_model_config.py` — seed `params[0]` is now the multimodal model. `test_base_module.py` — `_modules["chat"]` replaced with `_modules["multimodal"]`. `conftest.py` — `reasoning_model = params[0]` (first seed entry, was chat).

> **Note (v9.2):** Reasoning retry-on-empty in `app/queue.py` is covered by existing queue tests (no new tests — the change reuses the same streaming path with an extra attempt loop). Smart SD offload level selection in `modules/sd_cpp.py` is covered by existing SD module tests (`test_sd_cpp_module.py`). RAG reconnection (`check_availability()`) tested implicitly via existing RAG tests. `query_points()` migration in `modules/rag.py` covered by existing RAG tests.

> **v9.1 change:** `_fetch_page_content()` in `tests/test_search_module.py` is tested implicitly via existing search tests (no new tests needed — existing tests mock `requests` and do not trigger page fetch). `test_search_sends_correct_params` verifies correct POST parameters. All 15 search module tests pass.

## Test Structure

- **Fixtures** in `tests/conftest.py`:
  - `test_app` — isolated app + temp dirs
  - `client` — Flask test client
  - `runner` — CLI runner

## Mocking External Services

**External services are ALWAYS mocked**:
- Redis (`redis.from_url`)
- llama.cpp (`app.llamacpp_client.LlamaCppClient`)
- Qdrant (`modules.rag.QdrantClient`)

## Database Mode

- **Mock by default** (no `DATABASE_URL`).
- **In CI** (`DATABASE_URL` set) — real PostgreSQL with `TRUNCATE` between tests via `test_app` teardown.

## Background Workers

`RedisRequestQueue` threads are stopped via `stop_workers(timeout=3)` in `test_app` teardown to prevent pytest hang.

## Available Markers

- `unit` — fast unit tests
- `integration` — tests requiring external services (mocked)
- `e2e` — end-to-end tests
- `slow` — long-running tests
- `requires_db` — tests requiring PostgreSQL
- `requires_redis` — tests requiring Redis

### Running Tests by Marker

```bash
pytest                           # all tests
pytest -m unit                   # only unit tests
pytest -m "not slow"             # skip slow tests
pytest -m "not e2e"              # skip e2e tests
pytest --cov=app --cov=modules --cov-report=html  # with coverage
pytest tests/test_admin_routes.py  # specific file
```

### Test Examples

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

### Known Test Issues
  - **Unit test speed:** `CamModule` has 5×2s init retries, making `test_cam.py` ~10s per fixture. Not blocking, but slow.
  - **Load tests** (`tests/load/`) excluded from pytest collection — require locust fixtures. Run separately: `locust -f tests/load/locustfile.py --host http://localhost:5000` or `locust -f tests/load/locustfile_public.py --host http://localhost:5000` for public endpoints.

### Test Infrastructure Fixes (v9.0)
  - `tests/test_backups.py`: `Babel(flask_app)` added.
  - `tests/test_resource_manager.py`: `patch("app.resource_manager.requests.X", new=mock)`.
  - `app/routes/backups.py:restore_backup()`: `dirs_exist_ok=True`.
  - `tests/test_morph.py` **(NEW):** 16 tests for pymorphy3 morphological analysis.

## Configuration

When adding or changing environment variables in `app/config.py`, both `.env` and `.env.example` MUST be updated. `.env` contains real values; `.env.example` has placeholders and comments. Section order must match.