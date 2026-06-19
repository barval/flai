# app/tasks/dry_load.py
"""Background dry-load test with auto-rollback.

After admin saves a new model config, this module:
1. Schedules a background thread
2. Tries to load the new model via llama-swap
3. Verifies /v1/models endpoint returns the new model
4. On failure: rolls back to fallback model
5. On success: unloads the test model (it'll be loaded on next user request)
"""
import contextlib
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

DRY_LOAD_TIMEOUT_S = 30
DRY_LOAD_POLL_INTERVAL_S = 1

# Fallback model names per module (verified working on RTX 5060 Ti 16GB)
FALLBACK_MODELS: dict[str, str] = {
    "chat": "Qwen3-4B-Instruct-2507-MXFP4_MOE",
    "reasoning": "gpt-oss-20b-mxfp4",
    "multimodal": "Qwen3VL-8B-Instruct-Q4_K_M",
    "embedding": "bge-m3-Q8_0",
}


def _trigger_load(swap_url: str, module: str) -> bool:
    """Trigger llama-swap to load a model by hitting /v1/models.

    llama-swap auto-loads the model referenced in its config when the
    matching group is requested.  We hit a minimal completion request
    that won't be sent to the user.
    """
    import requests

    try:
        # Touch the model with a tiny completion — llama-swap will swap
        # in the model on first use.
        resp = requests.post(
            f"{swap_url.rstrip('/')}/v1/chat/completions",
            json={
                "model": module,
                "messages": [{"role": "user", "content": "."}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=DRY_LOAD_TIMEOUT_S,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.debug(f"dry_load trigger for {module} failed: {e}")
        return False


def _check_running(swap_url: str, expected: str) -> bool:
    """Check if a model is currently loaded (running) in llama-swap."""
    import requests

    try:
        resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=5)
        if resp.status_code != 200:
            return False
        running = resp.json().get("running", [])
        for m in running:
            name = m.get("name", "")
            model_id = m.get("model_id", "")
            if expected in (name, model_id):
                return True
        return False
    except Exception:
        return False


def _rollback(app: Any, module: str, failed_model: str) -> bool:
    """Roll back to a fallback model after dry-load failure."""
    app_obj = app._get_current_object() if hasattr(app, "_get_current_object") else app  # type: ignore[attr-defined]

    from app.database import get_db
    from app.model_config import invalidate_model_config_cache

    fallback = FALLBACK_MODELS.get(module)
    if not fallback:
        logger.error(f"No fallback model for module={module}")
        return False

    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """
                UPDATE model_configs
                SET model_name = %s, updated_at = CURRENT_TIMESTAMP
                WHERE module = %s
            """,
                (fallback, module),
            )
            conn.commit()
        invalidate_model_config_cache(module)

        # Regenerate llama-swap config
        from app.llama_swap_config import generate_and_write

        if generate_and_write(app_obj, include_preload=True):
            from app.llama_swap_config import LlamaSwapConfigGenerator

            gen = LlamaSwapConfigGenerator(app_obj)
            gen.signal_reload()
            logger.warning(
                f"Auto-rollback: {module} reverted to {fallback} "
                f"(was {failed_model})"
            )
            return True
        return False
    except Exception as e:
        logger.exception(f"Rollback failed for {module}: {e}")
        return False


def _dry_load_worker(app: Any, module: str, new_model: str) -> None:
    """Background thread: try loading the new model, rollback on failure."""
    import os

    import requests

    with app.app_context():
        swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
        logger.info(f"dry_load: starting for module={module} model={new_model}")

        # 1. Trigger model load via llama-swap
        success = _trigger_load(swap_url, module)

        # 2. Wait briefly for model to actually load
        deadline = time.time() + DRY_LOAD_TIMEOUT_S
        loaded = False
        while time.time() < deadline:
            if _check_running(swap_url, module):
                loaded = True
                break
            time.sleep(DRY_LOAD_POLL_INTERVAL_S)

        if loaded:
            logger.info(f"dry_load: {module}/{new_model} loaded OK")
            # Unload the test instance — next user request will load it fresh
            with contextlib.suppress(Exception):
                requests.post(
                    f"{swap_url.rstrip('/')}/api/models/unload",
                    json={"model": module},
                    timeout=5,
                )
            return

        # 3. Load failed — rollback
        if not success:
            logger.warning(
                f"dry_load: {module}/{new_model} failed to load — rolling back"
            )
        else:
            logger.warning(
                f"dry_load: {module}/{new_model} didn't reach 'running' state — rolling back"
            )
        _rollback(app, module, new_model)


def schedule_dry_load(app: Any, module: str, new_model: str) -> None:
    """Schedule a background dry-load test for the new model.

    Safe to call from request handlers.  The thread is daemon, so
    it won't block process exit.
    """
    if not new_model:
        return

    thread = threading.Thread(
        target=_dry_load_worker,
        args=(app, module, new_model),
        daemon=True,
        name=f"dry-load-{module}",
    )
    thread.start()
    logger.info(
        f"dry_load: scheduled for module={module} model={new_model}"
    )
