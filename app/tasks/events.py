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
    from app.routes.tasks import _token_totals_for

    plan = task.get("step_plan") or {}
    steps = plan.get("steps", []) if isinstance(plan, dict) else (plan or [])
    payload = {
        "task_id": task.get("id"),
        "status": task.get("status"),
        "title": task.get("title") or "",
        "next_step_index": task.get("next_step_index") or 0,
        "total_steps": len(steps or []),
        "abort_reason": task.get("abort_reason"),
        "exec_seconds": float(task.get("accumulated_exec_seconds") or 0.0),
        "token_totals": _token_totals_for([task.get("id")]) or {"prompt_tokens": 0, "completion_tokens": 0},
    }
    # Add queue position for queued tasks (the list renders the ⏳ position badge)
    if task.get("status") == "queued":
        from app.database import get_queued_task_positions

        positions = get_queued_task_positions()
        if positions:
            payload["queue_position"] = positions.get(str(task.get("id"))) or 0
    return payload


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
