import json

import redis as redis_lib
from flask import Blueprint, Response, current_app, jsonify, session, stream_with_context

bp = Blueprint("events", __name__, url_prefix="/api/events")


@bp.route("/stream")
def event_stream():
    if "login" not in session:
        return jsonify({"error": "Not authorized"}), 401
    user_id = session["login"]

    def generate():
        r = redis_lib.from_url(current_app.config["REDIS_URL"], decode_responses=True)
        pubsub = r.pubsub()
        pubsub.subscribe(f"user:events:{user_id}")

        # Send initial connected event
        yield f"event: connected\ndata: {json.dumps({'status': 'ok', 'user_id': user_id})}\n\n"

        try:
            while True:
                msg = pubsub.get_message(timeout=20.0)
                if msg and msg["type"] == "message":
                    data = msg["data"]
                    yield f"data: {data}\n\n"
                else:
                    # Heartbeat to keep connection alive through proxies
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            try:
                pubsub.unsubscribe()
                r.close()
            except Exception:
                pass

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
