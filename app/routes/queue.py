# app/routes/queue.py
from flask import Blueprint, current_app, jsonify, request, session
from flask_babel import gettext as _

bp = Blueprint("queue", __name__, url_prefix="/api/queue")


@bp.route("/internal/sd_preview", methods=["POST"])
def api_sd_preview():
    """Internal endpoint for sd-wrapper to publish image preview via SSE.

    sd-wrapper calls this endpoint when a new preview frame is available during generation.
    The preview is published to the user's SSE stream as an 'image_preview' event.
    """
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    user_id = data.get("user_id")
    session_id = data.get("session_id")
    task_id = data.get("task_id")
    image_b64 = data.get("image_b64")
    step = data.get("step")
    total = data.get("total")

    if not user_id or not image_b64:
        return jsonify({"error": "Missing user_id or image_b64"}), 400

    from app.events import get_events_publisher

    publisher = get_events_publisher()
    if publisher is None:
        return jsonify({"error": "Events publisher unavailable"}), 503

    publisher.publish(
        user_id,
        "image_preview",
        {
            "task_id": task_id,
            "session_id": session_id,
            "image_b64": image_b64,
            "step": step,
            "total": total,
        },
    )
    return jsonify({"status": "ok"})


@bp.route("/internal/sd_step", methods=["POST"])
def api_sd_step():
    """Internal endpoint for sd-wrapper to publish image generation/editing step progress via SSE."""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    user_id = data.get("user_id")
    session_id = data.get("session_id")
    task_id = data.get("task_id")
    step = data.get("step")
    total = data.get("total")

    if not user_id or step is None or total is None:
        return jsonify({"error": "Missing user_id, step, or total"}), 400

    from app.events import get_events_publisher

    publisher = get_events_publisher()
    if publisher is None:
        return jsonify({"error": "Events publisher unavailable"}), 503

    publisher.publish(
        user_id,
        "image_step",
        {
            "task_id": task_id,
            "session_id": session_id,
            "step": step,
            "total": total,
            "percent": round(step / total * 100) if total > 0 else 0,
        },
    )
    return jsonify({"status": "ok"})


@bp.route("/status", methods=["GET"])
def api_queue_status():
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    user_id = session["login"]
    lang = session.get("language", "ru")
    status = current_app.request_queue.get_user_requests_status(user_id, lang=lang)
    queue_length = current_app.request_queue.redis.llen(current_app.request_queue.queue_key)
    status["system"] = {
        "total_queued": queue_length,
        "current_load": "high" if queue_length > 10 else "normal",
        "avg_response_time": 5,
    }
    return jsonify(status)


@bp.route("/counts", methods=["GET"])
def api_queue_counts():
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401
    user_id = session["login"]
    user_queued, total_queued = current_app.request_queue.get_user_queue_counts(user_id)
    return jsonify({"user_queued": user_queued, "total_queued": total_queued})


@bp.route("/result/<request_id>", methods=["GET"])
def api_check_result(request_id):
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401

    # Check if the result exists — request_id is a UUID known only to the
    # client that submitted the request, so possession of the ID implies ownership.
    result = current_app.request_queue.check_result(request_id)
    if result and result.get("status") in ("completed", "error"):
        return jsonify(result)

    return jsonify({"status": "pending"})
