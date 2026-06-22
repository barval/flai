import contextlib
import json
import time

import redis as redis_lib


class EventsPublisher:
    """Publishes real-time events to users via Redis pub/sub.

    The SSE endpoint subscribes to ``user:events:<user_id>`` channels.
    """

    def __init__(self, redis_url: str):
        self._redis = redis_lib.from_url(redis_url, decode_responses=True)

    def publish(self, user_id: str, event_type: str, data: dict) -> None:
        """Publish an event to the user's Redis pub/sub channel."""
        payload = json.dumps(
            {"type": event_type, "data": data, "timestamp": time.time()},
            ensure_ascii=False,
        )
        with contextlib.suppress(Exception):
            self._redis.publish(f"user:events:{user_id}", payload)

    def close(self):
        with contextlib.suppress(Exception):
            self._redis.close()


_publisher: EventsPublisher | None = None


def get_events_publisher(app=None) -> EventsPublisher | None:
    """Return the singleton EventsPublisher, initialising from *app* if needed."""
    global _publisher
    if _publisher is None and app is not None:
        redis_url = app.config.get("REDIS_URL", "redis://localhost:6379/0")
        _publisher = EventsPublisher(redis_url)
    return _publisher


def init_events_publisher(app):
    """Initialise the publisher at app startup and attach it to *app*."""
    publisher = get_events_publisher(app)
    app.events_publisher = publisher
    return publisher
