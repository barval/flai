# Testing — FLAI v9.0

This document describes the testing infrastructure, fixtures, mocking strategy, and known test issues. Read it when writing or running tests.

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

27 tests for rule-based SLM fact extraction: sentence splitting, scoring by category patterns (preferences, facts, instructions, personality), text normalization, Levenshtein similarity, fact extraction, deduplication, and explicit remember parsing.

`tests/test_slm_merge_rules.py` **(NEW in v9.0)**

22 tests for rule-based SLM fact merging: fast_cleanup (exact duplicates, fragments), edit_distance_merge (Levenshtein near-duplicates), fragment_merge (stricter substring detection), temporal_decay (auto-archive old low-confidence facts), and merge scheduling logic.

`tests/test_backups.py`

Fixed in v9.0:
`Babel(flask_app)` added to `app` fixture (was causing KeyError 'babel')
`test_restore_backup` fixed via `dirs_exist_ok=True` in `app/routes/backups.py:restore_backup()` (was causing `shutil.copytree FileExistsError` on `data/slm`)

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