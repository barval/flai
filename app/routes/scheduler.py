# app/routes/scheduler.py
"""The user's scheduled items: list and delete.

There is no editor on purpose: changing a schedule is delete-and-recreate from chat, so
there is one creation path and the panel stays a list. Deleting cancels whatever the item
already started — a ghost message from a schedule the user removed is what they would
report as a bug.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, jsonify, session
from flask_babel import gettext as _

from app import scheduler_store

bp = Blueprint("scheduler", __name__, url_prefix="/api/scheduler")

def _item_view(item: dict[str, Any]) -> dict[str, Any]:
    next_run = item.get("next_run_at")
    return {
        "id": str(item.get("id")),
        "kind": item.get("kind"),
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "repeat": item.get("repeat") or "once",
        "every_minutes": item.get("every_minutes"),
        "enabled": bool(item.get("enabled")),
        "timezone": item.get("timezone"),
        "next_run_at": next_run.isoformat() if hasattr(next_run, "isoformat") else None,
        "created_at": item["created_at"].isoformat() if hasattr(item.get("created_at"), "isoformat") else None,
    }


def _cancel_item_runs(item_id: str) -> int:
    """Cancel what the item already started and silence its delivery.

    A run that is executing notices between rounds because its row stops being `running`;
    a run still queued is cancelled here, and both are marked delivered so the message
    never arrives for a schedule the person removed.
    """
    from app import scheduler_runs

    return scheduler_runs.cancel_for_item(item_id, datetime.now(UTC))


@bp.route("/items", methods=["GET"])
def list_items():
    login = session.get("login")
    if not login:
        return jsonify({"error": "⚠️ " + _("Not authenticated")}), 401
    return jsonify({"items": [_item_view(item) for item in scheduler_store.list_items(login)]})


@bp.route("/items/<item_id>/delete", methods=["POST"])
def delete_item(item_id: str):
    login = session.get("login")
    if not login:
        return jsonify({"error": "⚠️ " + _("Not authenticated")}), 401
    item = scheduler_store.get_item(item_id)
    if item is None or item.get("user_id") != login:
        return jsonify({"error": "⚠️ " + _("Not found")}), 404

    cancelled = _cancel_item_runs(item_id)
    scheduler_store.delete_item(item_id)
    return jsonify({"ok": True, "cancelled": cancelled})
