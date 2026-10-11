# app/tasks/gate.py
"""Scheduler gate for the Tasks subsystem.

The TaskExecutor (v12.6) may only run task steps while the user is idle:
GPU free, all request queues empty, and no chat activity for ``quiet_s``
seconds. ``chat_activity_now()`` records the sliding-window timestamp at
every chat-bearing enqueue; ``is_gate_open()`` is the polling predicate.
Queue emptiness and GPU business are injected as callables by the caller
(the executor owns the real Redis queue keys and the resource manager) —
this module stays dependency-free (stdlib only, no app.queue import).
"""

from __future__ import annotations

import time

LAST_CHAT_KEY = "task:last_chat_activity"


def chat_activity_now(redis_client) -> None:
    """Record a chat activity timestamp (sliding window, time.time() string)."""
    redis_client.set(LAST_CHAT_KEY, str(time.time()))


def is_gate_open(redis_client, quiet_s: int, gpu_busy, queues_empty) -> bool:
    """True iff GPU is free, queues are empty and chat has been quiet >= quiet_s.

    ``gpu_busy`` and ``queues_empty`` are zero-arg callables injected by the
    caller. No last-activity record counts as quiet (gate open).
    """
    if gpu_busy():
        return False
    if not queues_empty():
        return False
    last = redis_client.get(LAST_CHAT_KEY)
    if last is None:
        return True
    return (time.time() - float(last)) >= quiet_s
