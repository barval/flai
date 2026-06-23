"""Tests for SSE event system (EventsPublisher + /api/events/stream)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.events import EventsPublisher, get_events_publisher, init_events_publisher

# ── EventsPublisher unit tests ──────────────────────────────────────────


class TestEventsPublisher:
    def test_publish_calls_redis_publish(self):
        mock_redis = MagicMock()
        publisher = EventsPublisher.__new__(EventsPublisher)
        publisher._redis = mock_redis

        publisher.publish("user1", "message_new", {"msg_id": 42})

        mock_redis.publish.assert_called_once()
        channel = mock_redis.publish.call_args[0][0]
        payload = json.loads(mock_redis.publish.call_args[0][1])
        assert channel == "user:events:user1"
        assert payload["type"] == "message_new"
        assert payload["data"]["msg_id"] == 42
        assert "timestamp" in payload

    def test_publish_multiple_events(self):
        mock_redis = MagicMock()
        publisher = EventsPublisher.__new__(EventsPublisher)
        publisher._redis = mock_redis

        publisher.publish("user1", "message_new", {"msg_id": 1})
        publisher.publish("user1", "result_completed", {"request_id": "abc"})
        publisher.publish("user2", "message_new", {"msg_id": 2})

        assert mock_redis.publish.call_count == 3

    def test_publish_exception_does_not_propagate(self):
        mock_redis = MagicMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        publisher = EventsPublisher.__new__(EventsPublisher)
        publisher._redis = mock_redis

        # Should not raise
        publisher.publish("user1", "test", {"key": "val"})

    def test_close_calls_redis_close(self):
        mock_redis = MagicMock()
        publisher = EventsPublisher.__new__(EventsPublisher)
        publisher._redis = mock_redis

        publisher.close()
        mock_redis.close.assert_called_once()

    def test_close_exception_does_not_propagate(self):
        mock_redis = MagicMock()
        mock_redis.close.side_effect = ConnectionError("close failed")
        publisher = EventsPublisher.__new__(EventsPublisher)
        publisher._redis = mock_redis

        publisher.close()  # should not raise

    def test_publish_unicode_content(self):
        mock_redis = MagicMock()
        publisher = EventsPublisher.__new__(EventsPublisher)
        publisher._redis = mock_redis

        publisher.publish("user1", "message_new", {"text": "Привет, мир!"})
        payload = json.loads(mock_redis.publish.call_args[0][1])
        assert payload["data"]["text"] == "Привет, мир!"


# ── get_events_publisher / init_events_publisher tests ─────────────────


class TestPublisherSingleton:
    def setup_method(self):
        import app.events as events_mod
        events_mod._publisher = None

    def test_get_without_app_returns_none(self):
        assert get_events_publisher() is None

    def test_get_with_app_creates_publisher(self):
        app = MagicMock()
        app.config = {"REDIS_URL": "redis://test:6379/0"}

        publisher = get_events_publisher(app)
        assert publisher is not None
        assert isinstance(publisher, EventsPublisher)

    def test_init_attaches_to_app(self):
        app = MagicMock()
        app.config = {"REDIS_URL": "redis://test:6379/0"}

        publisher = init_events_publisher(app)
        assert app.events_publisher is publisher

    def test_singleton_same_instance(self):
        app = MagicMock()
        app.config = {"REDIS_URL": "redis://test:6379/0"}

        p1 = get_events_publisher(app)
        p2 = get_events_publisher(app)
        assert p1 is p2


# ── SSE endpoint tests ──────────────────────────────────────────────────


class TestSSEEndpoint:
    def test_stream_requires_auth(self, client):
        resp = client.get("/api/events/stream")
        assert resp.status_code == 401

    def test_stream_returns_eventsource_headers(self, client, test_app):
        with client.session_transaction() as sess:
            sess["login"] = "testuser"

        resp = client.get("/api/events/stream")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        assert resp.headers.get("Cache-Control") == "no-cache"
        assert resp.headers.get("X-Accel-Buffering") == "no"

    def test_stream_sends_connected_event(self, client, test_app):
        with client.session_transaction() as sess:
            sess["login"] = "testuser"

        with patch("app.routes.events.redis_lib.from_url") as mock_redis_factory:
            mock_redis = MagicMock()
            mock_pubsub = MagicMock()
            mock_pubsub.get_message.return_value = None
            mock_redis.pubsub.return_value = mock_pubsub
            mock_redis_factory.return_value = mock_redis

            resp = client.get("/api/events/stream")
            first_chunk = next(resp.response).decode()

            assert "connected" in first_chunk
            assert "testuser" in first_chunk

    def test_stream_publishes_and_receives_event(self, client, test_app):
        with client.session_transaction() as sess:
            sess["login"] = "testuser"

        with patch("app.routes.events.redis_lib.from_url") as mock_redis_factory:
            mock_redis = MagicMock()
            mock_pubsub = MagicMock()
            mock_redis.pubsub.return_value = mock_pubsub

            test_event = json.dumps(
                {
                    "type": "message_new",
                    "data": {"msg_id": 42, "content": "Hello"},
                    "timestamp": 1234567890.0,
                }
            )
            calls = iter(
                [
                    None,
                    {"type": "message", "data": test_event},
                ]
            )

            def mock_get_message(*args, **kwargs):
                try:
                    return next(calls)
                except StopIteration:
                    return None

            mock_pubsub.get_message.side_effect = mock_get_message
            mock_redis_factory.return_value = mock_redis

            resp = client.get("/api/events/stream")
            chunks = []
            for i, chunk in enumerate(resp.response):
                if i >= 3:
                    break
                chunks.append(chunk.decode())
            data = "".join(chunks)

            assert ": heartbeat" in data
            assert '"msg_id": 42' in data
            assert '"type": "message_new"' in data


# ── Integration: EventsPublisher + save_message via queue ───────────────


@pytest.mark.unit
def test_events_publisher_integration(test_app):
    """Verify that the publisher is accessible from app context."""
    assert hasattr(test_app, "events_publisher")
    publisher = test_app.events_publisher
    assert publisher is not None
    assert isinstance(publisher, EventsPublisher)
