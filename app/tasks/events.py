# app/tasks/events.py
"""Task lifecycle events on the existing per-user SSE channel.

There is one stream per user (``user:events:<login>``, served by
``/api/events/stream``); task updates piggyback on it with the ``task_update``
type so the Tasks panel refreshes without polling. Best effort by design — a
publish failure never breaks an HTTP request or an executor cycle.
"""

from __future__ import annotations

from typing import Any

from app.events import get_events_publisher


def _task_event_payload(task: dict) -> dict:
    """Small, path-free task snapshot for the client."""
    plan = task.get("step_plan") or {}
    steps = plan.get("steps", []) if isinstance(plan, dict) else (plan or [])
    return {
        "task_id": task.get("id"),
        "status": task.get("status"),
        "title": task.get("title") or "",
        "next_step_index": task.get("next_step_index") or 0,
        "total_steps": len(steps or []),
        "abort_reason": task.get("abort_reason"),
    }


def publish_task_update(app: Any, task: dict | None) -> None:
    """Publish one ``task_update`` event for *task* (no-op on any failure)."""
    if not task:
        return
    user_id = task.get("user_id")
    if not user_id:
        return
    try:
        publisher = get_events_publisher(app)
        if publisher is None:
            return
        publisher.publish(user_id, "task_update", _task_event_payload(task))
    except Exception:  # noqa: BLE001 — SSE is advisory, never fatal
        return


def publish_task_stage(app: Any, task: dict | None, stage: str | None, count: Any = None) -> None:
    """Publish one ``task_stage`` event (plan-phase progress; no-op on failure).

    Same per-user stream as ``task_update``, but a distinct type: the task
    view renders it as a live stage line with a ticking counter (chat parity
    with ``task_progress``), while the chat render paths ignore it. An empty
    stage clears the current one. *count* fills the ``%s`` of a numbered stage
    (e.g. ``rlm_step`` — the phase number); without it the label keeps the
    placeholder literally.
    """
    if not task:
        return
    user_id = task.get("user_id")
    if not user_id:
        return
    try:
        publisher = get_events_publisher(app)
        if publisher is None:
            return
        payload = {"task_id": task.get("id"), "stage": stage or ""}
        if count is not None:
            payload["count"] = count
        publisher.publish(user_id, "task_stage", payload)
    except Exception:  # noqa: BLE001 — SSE is advisory, never fatal
        return
