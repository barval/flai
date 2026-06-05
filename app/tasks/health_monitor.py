# app/tasks/health_monitor.py
"""Watchdog: detect llama-swap crash loops and auto-rollback to fallback model.

Runs in a daemon thread, polling llama-swap /running every 60 seconds.
If a model is crashing repeatedly (3 failures within 5 minutes),
automatically rolls back to the fallback model.
"""
import logging
import os
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

WATCHDOG_INTERVAL_S = 60
WATCHDOG_FAILURE_WINDOW_S = 300  # 5 minutes
WATCHDOG_FAILURE_THRESHOLD = 3   # 3 failures in window triggers rollback

# Track recent failures per module: {module: deque[timestamp]}
_failures: dict[str, deque[float]] = {}
_lock = threading.Lock()


def _get_running(swap_url: str) -> list[dict[str, Any]]:
    """Fetch currently running models from llama-swap."""
    import requests

    try:
        resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            running: list[dict[str, Any]] = data.get("running", [])
            return running
        logger.warning(f"llama-swap /running returned {resp.status_code}")
        return []
    except Exception as e:
        logger.debug(f"llama-swap /running error: {e}")
        return []


def _try_health_check(swap_url: str, module: str) -> bool:
    """Send a tiny completion to verify the model is actually working."""
    import requests

    try:
        resp = requests.post(
            f"{swap_url.rstrip('/')}/v1/chat/completions",
            json={
                "model": module,
                "messages": [{"role": "user", "content": "."}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=30,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.debug(f"Health check for {module} failed: {e}")
        return False


def _record_failure(module: str) -> int:
    """Record a failure timestamp.  Returns the count of failures in the window."""
    now = time.time()
    with _lock:
        if module not in _failures:
            _failures[module] = deque()
        dq = _failures[module]
        dq.append(now)
        # Trim old entries
        while dq and dq[0] < now - WATCHDOG_FAILURE_WINDOW_S:
            dq.popleft()
        return len(dq)


def _clear_failures(module: str) -> None:
    with _lock:
        _failures.pop(module, None)


def _auto_rollback(app: Any, module: str) -> bool:
    """Roll back to the fallback model.  Returns True on success."""
    from app.tasks.dry_load import FALLBACK_MODELS, _rollback

    fallback = FALLBACK_MODELS.get(module)
    if not fallback:
        logger.error(f"watchdog: no fallback for module={module}")
        return False
    logger.warning(
        f"watchdog: auto-rolling back {module} to {fallback} "
        f"due to crash loop"
    )
    return _rollback(app, module, fallback)


def _watchdog_loop(app: Any) -> None:
    """Main watchdog loop.  Polls /running + health-checks each model."""
    swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")

    # Wait a bit for app warmup
    time.sleep(30)

    while True:
        try:
            with app.app_context():
                running = _get_running(swap_url)
                if not running:
                    # Nothing loaded — nothing to monitor
                    time.sleep(WATCHDOG_INTERVAL_S)
                    continue

                for model in running:
                    module = model.get("name", "")
                    if not module or module not in ("chat", "reasoning", "multimodal", "embedding"):
                        continue

                    # Try a health check
                    if _try_health_check(swap_url, module):
                        _clear_failures(module)
                    else:
                        failures = _record_failure(module)
                        logger.warning(
                            f"watchdog: {module} health check failed "
                            f"({failures}/{WATCHDOG_FAILURE_THRESHOLD} in window)"
                        )
                        if failures >= WATCHDOG_FAILURE_THRESHOLD:
                            _auto_rollback(app, module)
                            _clear_failures(module)
        except Exception as e:
            logger.exception(f"watchdog loop error: {e}")

        time.sleep(WATCHDOG_INTERVAL_S)


def start_watchdog(app: Any) -> None:
    """Start the watchdog thread.  Safe to call once at app startup."""
    thread = threading.Thread(
        target=_watchdog_loop,
        args=(app,),
        daemon=True,
        name="flai-watchdog",
    )
    thread.start()
    logger.info("Watchdog started: crash loop detection enabled")
