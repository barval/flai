# app/tasks/health_monitor.py
"""Watchdog: detect llama-swap crash loops and mark modules unhealthy.

Runs in a daemon thread, polling llama-swap /running every 60 seconds.
If a model is crashing repeatedly (3 failures within 5 minutes), the
module is marked UNHEALTHY — the watchdog NEVER swaps the model by itself
(model choice is the admin's, and a hardcoded fallback that does not exist
on disk silently broke multimodal on a deployment). Health checks resume
after a cooldown; a successful check clears the unhealthy marker.
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
WATCHDOG_FAILURE_THRESHOLD = 3  # 3 failures in window triggers unhealthy mark
WATCHDOG_UNHEALTHY_COOLDOWN_S = 600  # pause health checks for 10 min after marking
LTX_OOM_WINDOW_S = 3600  # 1 hour sliding window for OOM metric


def is_gpu_busy() -> bool:
    """True while the GPU queue is running a transaction (SD, video, VRAM wait).

    The watchdog must stay away from llama-swap during those windows: a
    health check issued while ensure_vram_for is waiting for VRAM respawns
    the model being unloaded, so the wait can never satisfy its threshold.
    """
    from app.resource_manager import get_resource_manager

    try:
        return get_resource_manager().is_gpu_busy()
    except Exception:
        return False


def _queue_gpu_busy(app: Any) -> bool:
    """True while a worker holds the queue's GPU lock.

    Covers plain model tasks that do NOT set the ResourceManager busy flags
    (multimodal chat, reasoning, image chat): a long CPU multimodal call keeps
    ``_gpu_lock`` held for minutes, and a health check fired into the busy
    model times out — previously counted as a crash-loop failure.
    """
    queue = getattr(app, "request_queue", None)
    try:
        return bool(queue and queue.is_gpu_task_active())
    except Exception:
        return False


# Track recent failures per module: {module: deque[timestamp]}
_failures: dict[str, deque[float]] = {}
# Modules marked unhealthy by the watchdog: {module: timestamp}
_unhealthy: dict[str, float] = {}
# Track recent ltx-video OOM events: deque[timestamp]
_ltx_video_oom_events: deque[float] = deque()
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
        # Reasoning models (27B+) need more time to load and generate even 1 token
        timeout = 60 if module == "reasoning" else 30
        resp = requests.post(
            f"{swap_url.rstrip('/')}/v1/chat/completions",
            json={
                "model": module,
                "messages": [{"role": "user", "content": "."}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=timeout,
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


def record_ltx_video_oom() -> int:
    """Record an LTX-Video OOM event. Returns count in the last hour.

    Exposed in /admin/api/health for monitoring. Watchdog does not
    auto-rollback on OOM (different from llama-swap crash loop) — OOM
    is usually a transient GPU state issue that resolves on next request.
    """
    now = time.time()
    with _lock:
        _ltx_video_oom_events.append(now)
        cutoff = now - LTX_OOM_WINDOW_S
        while _ltx_video_oom_events and _ltx_video_oom_events[0] < cutoff:
            _ltx_video_oom_events.popleft()
        return len(_ltx_video_oom_events)


def get_ltx_video_oom_count() -> int:
    """Return count of LTX-Video OOM events in the last hour (thread-safe)."""
    now = time.time()
    with _lock:
        cutoff = now - LTX_OOM_WINDOW_S
        return sum(1 for t in _ltx_video_oom_events if t >= cutoff)


def _mark_unhealthy(module: str) -> None:
    """Mark a module unhealthy (no model swap — admin model choice is kept)."""
    now = time.time()
    with _lock:
        _unhealthy[module] = now
    logger.error(
        f"watchdog: {module} marked UNHEALTHY ({WATCHDOG_FAILURE_THRESHOLD} "
        f"failures in {WATCHDOG_FAILURE_WINDOW_S}s window). Model left unchanged — "
        "check its health manually"
    )


def _is_unhealthy(module: str) -> bool:
    with _lock:
        return module in _unhealthy


def _clear_unhealthy(module: str) -> None:
    with _lock:
        _unhealthy.pop(module, None)


def _in_unhealthy_cooldown(module: str) -> bool:
    """True while an unhealthy module is in its check-pause cooldown."""
    now = time.time()
    with _lock:
        marked = _unhealthy.get(module)
        if marked is None:
            return False
        return now - marked < WATCHDOG_UNHEALTHY_COOLDOWN_S


def get_unhealthy_modules() -> list[str]:
    """Return modules the watchdog has marked unhealthy (thread-safe)."""
    with _lock:
        return sorted(_unhealthy)


def _stop_event_for(app: Any) -> threading.Event:
    """Return the app's watchdog stop event, creating it on first use.

    The event lives on the app so a process that builds several apps (a test
    suite) can stop each of them independently.
    """
    event = getattr(app, "_watchdog_stop", None)
    if not isinstance(event, threading.Event):
        event = threading.Event()
        app._watchdog_stop = event
    return event


def _watchdog_loop(app: Any) -> None:
    """Main watchdog loop.  Polls /running + health-checks each model."""
    swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
    stop = _stop_event_for(app)

    # Wait a bit for app warmup (abortable: a test teardown must not wait 30s)
    if stop.wait(30):
        return

    while not stop.is_set():
        try:
            if is_gpu_busy() or _queue_gpu_busy(app):
                # GPU transaction or a worker model call in progress — skip the
                # whole tick. Touching llama-swap now would respawn the model
                # being unloaded, and health checks against a busy model time
                # out and inflate the crash-loop counters.
                if stop.wait(WATCHDOG_INTERVAL_S):
                    return
                continue

            with app.app_context():
                running = _get_running(swap_url)
                if not running:
                    # Nothing loaded — nothing to monitor
                    if stop.wait(WATCHDOG_INTERVAL_S):
                        return
                    continue

                for model in running:
                    module = model.get("name", "")
                    if not module or module not in ("reasoning", "multimodal", "embedding"):
                        continue

                    # An unhealthy module pauses health checks until the
                    # cooldown expires. The model was NOT touched — the admin
                    # decides what to do (nothing hides the crash better than
                    # a watchdog that "fixes" it by swapping the model).
                    if _in_unhealthy_cooldown(module):
                        continue

                    # Try a health check
                    if _try_health_check(swap_url, module):
                        _clear_failures(module)
                        if _is_unhealthy(module):
                            _clear_unhealthy(module)
                            logger.warning(f"watchdog: {module} recovered — cleared UNHEALTHY mark")
                    else:
                        failures = _record_failure(module)
                        logger.warning(
                            f"watchdog: {module} health check failed "
                            f"({failures}/{WATCHDOG_FAILURE_THRESHOLD} in window)"
                        )
                        if failures >= WATCHDOG_FAILURE_THRESHOLD:
                            _mark_unhealthy(module)
                            _clear_failures(module)
        except Exception as e:
            logger.exception(f"watchdog loop error: {e}")

        if stop.wait(WATCHDOG_INTERVAL_S):
            return


def start_watchdog(app: Any) -> None:
    """Start the watchdog thread.  Safe to call once at app startup."""
    _stop_event_for(app).clear()
    thread = threading.Thread(
        target=_watchdog_loop,
        args=(app,),
        daemon=True,
        name="flai-watchdog",
    )
    app._watchdog_thread = thread
    thread.start()
    logger.info("Watchdog started: crash loop detection enabled")


def stop_watchdog(app: Any, timeout: float = 5) -> bool:
    """Stop the watchdog thread of ``app``.  True when it is no longer running.

    The loop waits on its stop event instead of sleeping, so a stop request is
    honoured immediately instead of after the current poll interval.
    """
    thread = getattr(app, "_watchdog_thread", None)
    if not isinstance(thread, threading.Thread):
        return False
    _stop_event_for(app).set()
    thread.join(timeout=timeout)
    return not thread.is_alive()
