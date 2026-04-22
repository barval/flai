# AGENTS.md — FLAI v8.0

## Commands

```bash
# Lint (ruff)
ruff check .

# Type check (mypy — allowed to fail non-blocking)
mypy app/ modules/ --ignore-missing-imports || true

# Run all tests
pytest

# Run tests with coverage
pytest --cov=app --cov=modules --cov-report=html
```

CI runs in order: `lint → typecheck → test → docker-build`.

## Architecture

- **Flask app** (`wsgi.py` → `app.create_app()`) with modular blueprints under `app/routes/`.
- **`app/modules`** (legacy): `base.py` (chat/reasoning), `multimodal.py`, `sd_cpp.py`, `rag.py`, `audio.py`, `tts.py`, `cam.py`.
- **`modules/`** (new): same modules refactored as proper classes.
- **`app/llamacpp_client.py`**: single client for llama.cpp router (chat, reasoning, multimodal, embedding).
- **Config** loaded from `app/config.py` → env vars in `.env`.
- **DB**: PostgreSQL (`app/database.py`, `app/db.py`) + SQLite for user auth (`app/userdb.py`).
- **Queue**: Redis (`app/queue.py`), `RedisRequestQueue` — workers call `modules/` directly.
- **File serving**: `/api/files/<path>` with session ownership verification.

### Module initialization order (`app/__init__.py:143-188`)
1. `BaseModule`
2. `MultimodalModule`
3. `SdCppModule` (if `SD_WRAPPER_URL`)
4. `CamModule` (if `CAMERA_ENABLED`)
5. `RagModule` (if `QDRANT_URL`)
6. `AudioModule`
7. `TTSModule` (if `PIPER_URL`)
8. `RedisRequestQueue`

### llama.cpp router
Router mode (`--models-dir /models/`) with `models-preset.ini` generated at startup by `services/llamacpp/generate_presets.py` from `model_configs` DB table. Only one model in VRAM at a time (`--models-max 1`).

### Multimodal models
Must be in subdirectory named after the model, with `mmproj-*.gguf` inside.

## Testing

Fixtures are in `tests/conftest.py`. External services (Redis, llama.cpp, Qdrant) are mocked per-test via `patch`. Run `pytest` from repo root — `pytest.ini` sets `testpaths = tests`.

## Gotchas

- **Mock naming**: `create_mock_llamacpp()` / `mock_llamacpp_client` — `create_mock_ollama()` is a legacy alias.
- **Config loading order**: `app/config.py` → env vars — env must be set **before** `create_app()` is called.
- **CSRF**: `DEBUG_API_ENABLED=true` exempts the debug blueprint (`app/__init__.py:205`).
- **`models-preset.ini` is auto-generated** at container start — edits are overwritten. Use the admin panel.
- **`app.utils`**: prompt formatting, token estimation, context building.
- **`app/model_config.py`**: DB-backed per-model parameters (chat, reasoning, multimodal, embedding, chunks).
- **Reranker module** removed — re-ranking handled via LLM now.