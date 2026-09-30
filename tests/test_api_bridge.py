"""Tests for the API bridge: session resolution, enqueue, wait, streaming and embeddings."""

import base64
import json
import struct
from unittest.mock import Mock

import pytest
from flask import Flask

from app.api_bridge import (
    ApiImageRejectedError,
    ApiSessionNotFoundError,
    ApiTaskError,
    ApiTaskTimeoutError,
    _resolve_terminal,
    enqueue_chat,
    enqueue_embeddings,
    enqueue_transcription,
    normalize_chat_messages,
    resolve_api_session,
    serialize_chat_completion,
    serialize_embeddings,
    stream_chat,
    wait_for_result,
)

OWNED_SESSION = "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
FOREIGN_SESSION = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
SECOND_SESSION = "2a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
PARSED_IMAGE = {"file_data": "AAAA", "file_type": "image/png", "file_name": "image.png"}
IMAGE_URL_PART = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.set_calls = []
        self.closed = False
        self.hashes = {}
        self.sorted_sets = {}
        self.expirations = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.set_calls.append((key, value))
        self.store[key] = value

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def hsetnx(self, key, field, value):
        values = self.hashes.setdefault(key, {})
        if field in values:
            return False
        values[field] = value
        return True

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def zadd(self, key, mapping):
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zrevrange(self, key, start, stop, withscores=False):
        values = sorted(self.sorted_sets.get(key, {}).items(), key=lambda pair: pair[1], reverse=True)
        selected = values[start : stop + 1]
        return selected if withscores else [member for member, _score in selected]

    def zcard(self, key):
        return len(self.sorted_sets.get(key, {}))

    def zremrangebyrank(self, key, start, stop):
        values = self.sorted_sets.get(key, {})
        ordered = sorted(values, key=values.get)
        stop = len(ordered) + stop if stop < 0 else stop
        removed = ordered[start : stop + 1]
        for member in removed:
            values.pop(member, None)
        return len(removed)

    def eval(
        self,
        script,
        key_count,
        task_key,
        index_key,
        login,
        session_id,
        endpoint,
        created_at,
        ttl,
        task_id,
        maximum,
        only_if_absent,
    ):
        existing = self.hashes.get(task_key, {})
        if only_if_absent == "1" and existing:
            required = ("login", "session_id", "endpoint", "created_at")
            if any(key not in existing for key in required):
                return 0
            if (existing["login"], existing["session_id"], existing["endpoint"]) != (login, session_id, endpoint):
                return 0
            created_at = existing["created_at"]
        else:
            self.hashes[task_key] = {
                "login": login,
                "session_id": session_id,
                "endpoint": endpoint,
                "created_at": str(created_at),
            }
        self.zadd(index_key, {task_id: float(created_at)})
        if self.zcard(index_key) > int(maximum):
            self.zremrangebyrank(index_key, 0, -int(maximum) - 1)
        self.expire(task_key, int(ttl))
        self.expire(index_key, int(ttl))
        return 1

    def expire(self, key, seconds):
        self.expirations[key] = seconds

    def hdel(self, key, field):
        return self.hashes.get(key, {}).pop(field, None) is not None

    def close(self):
        self.closed = True


def api_user(login="alice", language="en"):
    return {"login": login, "language": language}


def stub_session(monkeypatch, factory):
    monkeypatch.setattr("app.api_bridge.create_session", lambda user_id, title=None, lang="ru": factory())
    monkeypatch.setattr("app.api_bridge.validate_session_ownership", lambda sid, login: sid == OWNED_SESSION)


@pytest.mark.unit
class TestResolveApiSession:
    def test_explicit_session_id_must_belong_to_api_user(self, monkeypatch):
        stub_session(monkeypatch, lambda: OWNED_SESSION)

        assert resolve_api_session(api_user(), OWNED_SESSION, "client-a") == OWNED_SESSION

    def test_foreign_session_is_rejected(self, monkeypatch):
        stub_session(monkeypatch, lambda: OWNED_SESSION)

        with pytest.raises(ApiSessionNotFoundError):
            resolve_api_session(api_user(), FOREIGN_SESSION, None)

    def test_client_user_maps_to_persistent_session(self, monkeypatch):
        redis = FakeRedis()
        monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: redis)
        stub_session(monkeypatch, lambda: OWNED_SESSION)

        first = resolve_api_session(api_user(), None, "home-assistant")
        second = resolve_api_session(api_user(), None, "home-assistant")

        assert first == second == OWNED_SESSION
        assert len(redis.set_calls) == 1
        assert redis.closed

    def test_missing_user_creates_fresh_session(self, monkeypatch):
        redis = FakeRedis()
        created = iter((OWNED_SESSION, SECOND_SESSION))
        monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: redis)
        monkeypatch.setattr("app.api_bridge.create_session", lambda user_id, title=None, lang="ru": next(created))

        assert resolve_api_session(api_user(), None, None) == OWNED_SESSION
        assert resolve_api_session(api_user(), None, None) == SECOND_SESSION
        assert redis.set_calls == []

    def test_stale_cached_session_is_replaced(self, monkeypatch):
        redis = FakeRedis()
        monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: redis)
        monkeypatch.setattr("app.api_bridge.create_session", lambda user_id, title=None, lang="ru": SECOND_SESSION)
        monkeypatch.setattr("app.api_bridge.validate_session_ownership", lambda sid, login: False)

        assert resolve_api_session(api_user(), None, "home-assistant") == SECOND_SESSION
        assert len(redis.set_calls) == 1

    def test_client_user_is_never_stored_raw_in_the_key(self, monkeypatch):
        redis = FakeRedis()
        monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: redis)
        stub_session(monkeypatch, lambda: OWNED_SESSION)

        resolve_api_session(api_user(), None, "home-assistant")

        key = redis.set_calls[0][0]
        assert key.startswith("api_conv:alice:")
        assert "home-assistant" not in key

    def test_foreign_client_map_never_leaks_between_users(self, monkeypatch):
        redis = FakeRedis()
        monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: redis)
        stub_session(monkeypatch, lambda: OWNED_SESSION)

        resolve_api_session(api_user(login="alice"), None, "shared-client")
        resolve_api_session(api_user(login="bob"), None, "shared-client")

        assert [key for key, _ in redis.set_calls] == [
            f"api_conv:alice:{redis.set_calls[0][0].rsplit(':', 1)[1]}",
            f"api_conv:bob:{redis.set_calls[1][0].rsplit(':', 1)[1]}",
        ]
        assert redis.set_calls[0][0].rsplit(":", 1)[1] == redis.set_calls[1][0].rsplit(":", 1)[1]


class FakePubSub:
    """A finite pub/sub stream: get_message pops one scripted message at a time."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.subscribed = None
        self.unsubscribed = False

    def subscribe(self, channel):
        self.subscribed = channel

    def unsubscribe(self):
        self.unsubscribed = True

    def get_message(self, timeout=None):
        if not self.messages:
            return None
        return self.messages.pop(0)


class FakeStreamingRedis(FakeRedis):
    def __init__(self, messages):
        super().__init__()
        self.pubsub_client = FakePubSub(messages)

    def pubsub(self):
        return self.pubsub_client


def result_event(task_id, status, result):
    envelope = {
        "type": "result_completed",
        "data": {"task_id": task_id, "session_id": "s1", "status": status, "result": result},
        "timestamp": 1.0,
    }
    return {"type": "message", "data": json.dumps(envelope), "channel": "user:events:alice"}


def assistant_result(text="OK", model_used="reasoning-model", prompt_tokens=11, completion_tokens=3):
    return {
        "response": text,
        "session_id": "s1",
        "model_used": model_used,
        "is_error": False,
        "message_id": 77,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


class FakeQueue:
    def __init__(self, results=None):
        self.added = []
        self.results = results or {}
        self.checked = []
        self.cancelled = []

    def add_request(self, user_id, session_id, request_data, user_class, lang="ru"):
        task_id = f"task-{len(self.added) + 1}"
        self.added.append((user_id, session_id, request_data, user_class, lang))
        return task_id, {"position": 1, "estimated_seconds": 3, "queue_type": "fast"}

    def check_result(self, request_id):
        self.checked.append(request_id)
        return self.results.get(request_id)

    def cancel_task(self, task_id):
        self.cancelled.append(task_id)
        return True


@pytest.fixture
def bridge_deps(monkeypatch):
    state = {"saved": [], "queue": FakeQueue(), "redis": None}
    monkeypatch.setattr("app.api_bridge.get_request_queue", lambda: state["queue"])
    monkeypatch.setattr(
        "app.api_bridge.save_message",
        lambda *args, **kwargs: state["saved"].append((args, kwargs)) or 4242,
    )
    monkeypatch.setattr("app.api_bridge.update_session_visit", lambda *args, **kwargs: None)

    def install(messages=None):
        state["redis"] = FakeStreamingRedis(messages or [])
        monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: state["redis"])
        return state["redis"]

    state["install"] = install
    return state


@pytest.mark.unit
class TestEnqueueChat:
    def test_text_turn_persists_and_submits_a_streaming_text_task(self, bridge_deps):
        task_id, info = enqueue_chat(api_user(), "s1", "Hello there")

        assert task_id == "task-1"
        assert info["queue_type"] == "fast"
        args, kwargs = bridge_deps["saved"][0]
        assert args[0] == "s1" and args[1] == "user"
        assert json.loads(args[2]) == [{"type": "text", "text": "Hello there"}]
        assert kwargs["user_id"] == "alice"
        assert kwargs["response_style"] == "neutral"

        user_id, session_id, request_data, user_class, lang = bridge_deps["queue"].added[0]
        assert (user_id, session_id) == ("alice", "s1")
        assert request_data["type"] == "text"
        assert request_data["text"] == "Hello there"
        assert request_data["stream"] is True
        assert request_data["current_message_id"] == 4242
        assert (user_class, lang) == (2, "en")

    def test_user_class_and_language_come_from_the_api_identity(self, bridge_deps):
        enqueue_chat({**api_user(), "service_class": 1, "language": "ru"}, "s1", "Hi")

        assert bridge_deps["queue"].added[0][3] == 1
        assert bridge_deps["queue"].added[0][4] == "ru"

    def test_image_turn_uses_the_image_task_type_and_saves_file_fields(self, bridge_deps):
        enqueue_chat(api_user(), "s1", "What is this?", [PARSED_IMAGE])

        args, kwargs = bridge_deps["saved"][0]
        assert args[3:6] == ("AAAA", "image/png", "image.png")
        assert kwargs["user_id"] == "alice"
        request_data = bridge_deps["queue"].added[0][2]
        assert request_data["type"] == "image"
        assert request_data["text"] == "What is this?"
        assert request_data["file_data"] == "AAAA"
        assert request_data["stream"] is True
        assert "current_message_id" not in request_data

    def test_image_only_turn_uses_the_image_task_type(self, bridge_deps):
        enqueue_chat(api_user(), "s1", "", [PARSED_IMAGE])

        assert bridge_deps["queue"].added[0][2]["type"] == "image"

    def test_queue_submission_is_attempted_even_when_persisting_fails(self, bridge_deps, monkeypatch):
        monkeypatch.setattr(
            "app.api_bridge.save_message",
            Mock(side_effect=RuntimeError("db down")),
        )
        with pytest.raises(RuntimeError):
            enqueue_chat(api_user(), "s1", "Hello")
        assert bridge_deps["queue"].added == []

    def test_session_is_marked_visited_for_the_shared_web_ui(self, bridge_deps, monkeypatch):
        visits = []
        monkeypatch.setattr("app.api_bridge.update_session_visit", lambda *args: visits.append(args))

        enqueue_chat(api_user(), "s1", "Hello")

        assert visits == [("alice", "s1")]


@pytest.mark.unit
class TestWaitForResult:
    def test_terminal_result_is_returned(self, bridge_deps):
        bridge_deps["install"]([result_event("task-1", "completed", assistant_result())])

        result = wait_for_result("alice", "task-1", 5)

        assert result["response"] == "OK"
        assert result["message_id"] == 77
        assert bridge_deps["redis"].pubsub_client.subscribed == "user:events:alice"
        assert bridge_deps["redis"].pubsub_client.unsubscribed
        assert bridge_deps["redis"].closed

    def test_nested_requeue_is_followed_to_the_next_task(self, bridge_deps):
        redis = bridge_deps["install"](
            [
                result_event("task-1", "completed", {"status": "queued", "request_id": "task-2"}),
                result_event("task-2", "completed", assistant_result("after requeue")),
            ]
        )
        redis.hset(
            "api:task:task-1",
            mapping={"login": "alice", "session_id": "s1", "endpoint": "/v1/chat/completions", "created_at": "1"},
        )
        bridge_deps["queue"].results["task-2"] = {
            "status": "completed",
            "result": assistant_result("after requeue"),
        }

        app = Flask(__name__)
        app.config["REDIS_RESULT_TTL"] = 3600
        with app.app_context():
            result = wait_for_result("alice", "task-1", 5)

        assert result["response"] == "after requeue"
        assert "task-2" in bridge_deps["queue"].checked

    def test_requeued_task_keeps_the_registry_owner(self, bridge_deps):
        redis = bridge_deps["install"](
            [
                result_event("task-1", "completed", {"status": "queued", "request_id": "task-2"}),
                result_event("task-2", "completed", assistant_result("after requeue")),
            ]
        )
        redis.hset(
            "api:task:task-1",
            mapping={"login": "alice", "session_id": "s1", "endpoint": "/v1/chat/completions", "created_at": "1"},
        )
        app = Flask(__name__)
        app.config["REDIS_RESULT_TTL"] = 3600
        with app.app_context():
            wait_for_result("alice", "task-1", 5)

        assert redis.hashes["api:task:task-2"]["login"] == "alice"
        assert redis.hashes["api:task:task-2"]["session_id"] == "s1"
        assert redis.hashes["api:task:task-2"]["endpoint"] == "/v1/chat/completions"

    def test_foreign_registered_requeue_child_is_not_followed(self, bridge_deps):
        redis = bridge_deps["install"](
            [result_event("task-1", "completed", {"status": "queued", "request_id": "task-2"})]
        )
        redis.hset(
            "api:task:task-1",
            mapping={"login": "alice", "session_id": "s1", "endpoint": "/v1/chat/completions", "created_at": "1"},
        )
        redis.hset(
            "api:task:task-2",
            mapping={"login": "bob", "session_id": "s2", "endpoint": "/v1/videos", "created_at": "2"},
        )
        bridge_deps["queue"].results["task-2"] = {
            "status": "completed",
            "result": assistant_result("foreign result"),
        }
        app = Flask(__name__)
        app.config["REDIS_RESULT_TTL"] = 3600

        with app.app_context(), pytest.raises(ApiTaskError) as excinfo:
            wait_for_result("alice", "task-1", 5)

        assert excinfo.value.localized(lambda msg: msg) == "Task task-1 failed"
        assert "task-2" not in bridge_deps["queue"].checked

    def test_events_for_another_task_are_ignored(self, bridge_deps):
        bridge_deps["install"]([result_event("other-task", "completed", assistant_result("nope"))])

        with pytest.raises(ApiTaskTimeoutError):
            wait_for_result("alice", "task-1", 0)

    def test_stream_tokens_are_not_mistaken_for_a_result(self, bridge_deps):
        bridge_deps["install"]([{"type": "message", "data": {"type": "stream_token", "data": {"task_id": "task-1"}}}])

        with pytest.raises(ApiTaskTimeoutError):
            wait_for_result("alice", "task-1", 0)

    def test_recovery_poll_finds_a_result_published_before_subscribing(self, bridge_deps):
        bridge_deps["queue"].results["task-1"] = {"status": "completed", "result": assistant_result("polled")}
        bridge_deps["install"]([])

        result = wait_for_result("alice", "task-1", 5)

        assert result["response"] == "polled"

    def test_error_status_raises_with_the_queue_message(self, bridge_deps):
        bridge_deps["install"]([result_event("task-1", "error", {"error": "⚠️ GPU memory unavailable"})])

        with pytest.raises(ApiTaskError) as excinfo:
            wait_for_result("alice", "task-1", 5)

        assert "⚠️ " in str(excinfo.value)

    def test_foreign_or_raw_queue_error_is_not_exposed_in_stream(self):
        from app.api_bridge import _stream_terminal

        chunks = list(
            _stream_terminal(
                "error",
                {"error": "private database connection string"},
                "task-1",
                1,
                "",
                "s1",
                False,
                lambda msg: msg,
            )
        )
        body = "".join(chunks)
        assert "private database connection string" not in body
        assert "Task task-1 failed" in body

    def test_timeout_reports_the_task_id_that_is_still_running(self, bridge_deps):
        bridge_deps["install"]([])

        with pytest.raises(ApiTaskTimeoutError) as excinfo:
            wait_for_result("alice", "task-1", 0)

        assert excinfo.value.task_id == "task-1"

    def test_pubsub_is_closed_even_when_the_wait_raises(self, bridge_deps):
        bridge_deps["install"]([])

        with pytest.raises(ApiTaskTimeoutError):
            wait_for_result("alice", "task-1", 0)

        assert bridge_deps["redis"].pubsub_client.unsubscribed
        assert bridge_deps["redis"].closed


@pytest.mark.unit
class TestSerializeChatCompletion:
    def test_result_becomes_an_openai_chat_completion(self):
        payload = serialize_chat_completion(assistant_result("Hi there"), "task-1", "s1")

        assert payload["object"] == "chat.completion"
        assert payload["id"] == "chatcmpl-task-1"
        assert payload["model"] == "flai-chat"
        assert isinstance(payload["created"], int)
        assert payload["choices"] == [
            {"index": 0, "message": {"role": "assistant", "content": "Hi there"}, "finish_reason": "stop"}
        ]
        assert payload["usage"] == {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}

    def test_usage_reports_only_the_known_sides(self):
        payload = serialize_chat_completion(
            assistant_result("Hi", prompt_tokens=None, completion_tokens=3),
            "task-1",
            "s1",
        )

        assert payload["usage"] == {"prompt_tokens": 0, "completion_tokens": 3, "total_tokens": 3}

    def test_session_id_is_exposed_for_conversation_continuity(self):
        payload = serialize_chat_completion(assistant_result(), "task-1", "s1")

        assert payload["flai_session_id"] == "s1"


@pytest.mark.unit
class TestNormalizeChatMessages:
    @pytest.fixture(autouse=True)
    def _app_context(self):
        app = Flask(__name__)
        app.config["MAX_IMAGE_SIZE"] = 1536
        with app.app_context():
            yield app

    def test_plain_string_content_is_joined(self):
        text, images = normalize_chat_messages(
            [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hello"}]
        )

        assert text == "Be brief.\nHello"
        assert images == []

    def test_text_and_image_parts_are_extracted(self, monkeypatch):
        monkeypatch.setattr(
            "app.utils.resize_image_if_needed", lambda data, ftype, name, size: (data, ftype, name, False, None, None)
        )
        text, images = normalize_chat_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        IMAGE_URL_PART,
                    ],
                }
            ]
        )

        assert text == "What is this?"
        assert images == [PARSED_IMAGE]

    def test_data_url_is_split_into_type_name_and_payload(self, monkeypatch):
        monkeypatch.setattr(
            "app.utils.resize_image_if_needed", lambda data, ftype, name, size: (data, ftype, name, False, None, None)
        )
        _, images = normalize_chat_messages([{"role": "user", "content": [IMAGE_URL_PART]}])

        assert images[0]["file_type"] == "image/png"
        assert images[0]["file_name"] == "image.png"
        assert images[0]["file_data"] == "AAAA"

    def test_resize_hook_is_applied_to_the_image(self, monkeypatch, _app_context):
        seen = {}

        def fake_resize(data, file_type, file_name, size):
            seen["max_size"] = size
            return "RESIZED", file_type, file_name, True, (1, 1), (2, 2)

        monkeypatch.setattr("app.utils.resize_image_if_needed", fake_resize)
        _app_context.config["MAX_IMAGE_SIZE"] = 999

        _, images = normalize_chat_messages([{"role": "user", "content": [IMAGE_URL_PART]}])

        assert seen["max_size"] == 999
        assert images[0]["file_data"] == "RESIZED"

    def test_first_image_wins_and_later_ones_are_dropped(self, monkeypatch):
        monkeypatch.setattr(
            "app.utils.resize_image_if_needed", lambda data, ftype, name, size: (data, ftype, name, False, None, None)
        )
        second = {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}}
        _, images = normalize_chat_messages([{"role": "user", "content": [IMAGE_URL_PART, second]}])

        assert images == [PARSED_IMAGE]

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/cat.png",
            "data:image/png,notbase64",
            "data:text/plain;base64,AAAA",
            "data:image/png;base64,",
        ],
    )
    def test_unusable_image_urls_are_rejected(self, url):
        with pytest.raises(ApiImageRejectedError):
            normalize_chat_messages([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}])

    def test_image_only_request_is_accepted(self, monkeypatch):
        monkeypatch.setattr(
            "app.utils.resize_image_if_needed", lambda data, ftype, name, size: (data, ftype, name, False, None, None)
        )
        text, images = normalize_chat_messages([{"role": "user", "content": [IMAGE_URL_PART]}])

        assert text == ""
        assert images == [PARSED_IMAGE]

    @pytest.mark.parametrize(
        "messages",
        [
            [],
            "not-a-list",
            [{"role": "user"}],
            [{"role": "bogus", "content": "hi"}],
            [{"role": "user", "content": 42}],
            [{"role": "user", "content": [{"type": "audio_url", "audio_url": {"url": "x"}}]}],
            [{"role": "user", "content": [{"type": "text", "text": "   "}]}],
        ],
    )
    def test_malformed_requests_are_rejected(self, messages):
        with pytest.raises(ApiImageRejectedError):
            normalize_chat_messages(messages)


def token_event(task_id, token, session_id="s1"):
    envelope = {
        "type": "stream_token",
        "data": {"task_id": task_id, "session_id": session_id, "token": token},
        "timestamp": 1.0,
    }
    return {"type": "message", "data": json.dumps(envelope), "channel": "user:events:alice"}


def progress_event(task_id, stage, session_id="s1"):
    envelope = {
        "type": "task_progress",
        "data": {"task_id": task_id, "session_id": session_id, "stage": stage},
        "timestamp": 1.0,
    }
    return {"type": "message", "data": json.dumps(envelope), "channel": "user:events:alice"}


def parse_sse(chunks):
    """Return (parsed_json_objects, done_count) from a list of raw SSE chunks."""
    payloads = []
    done = 0
    for chunk in chunks:
        for line in chunk.splitlines():
            if not line.startswith("data: "):
                continue
            body = line[len("data: ") :]
            if body == "[DONE]":
                done += 1
            else:
                payloads.append(json.loads(body))
    return payloads, done


def deltas(payloads):
    return [p["choices"][0]["delta"].get("content", "") for p in payloads if p.get("choices")]


class FakeClock:
    """Monotonic clock stub: replays *values*, then repeats the last one."""

    def __init__(self, values=None, step=1.0):
        self.values = list(values) if values else []
        self.step = step
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.values:
            index = min(self.calls - 1, len(self.values) - 1)
            return self.values[index]
        return self.calls * self.step


@pytest.fixture
def stream_deps(monkeypatch):
    state = {"queue": FakeQueue(), "redis": None, "clock": FakeClock()}
    monkeypatch.setattr("app.api_bridge.get_request_queue", lambda: state["queue"])
    monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: state["redis"])
    monkeypatch.setattr("app.api_bridge._now", lambda: state["clock"]())

    def install(messages):
        state["redis"] = FakeStreamingRedis(messages)
        return state

    state["install"] = install
    return state


@pytest.mark.unit
class TestStreamChat:
    def stream(self, stream_deps, messages, **kwargs):
        stream_deps["install"](messages)
        kwargs.setdefault("timeout_s", 30.0)
        return list(stream_chat("alice", "task-1", "s1", **kwargs))

    def test_tokens_are_forwarded_in_order_as_content_deltas(self, stream_deps):
        chunks = self.stream(
            stream_deps,
            [
                token_event("task-1", "Hel"),
                token_event("task-1", "lo "),
                token_event("task-1", "world"),
                result_event("task-1", "completed", assistant_result("Hello world")),
            ],
        )

        payloads, done = parse_sse(chunks)
        assert "".join(deltas(payloads)) == "Hello world"
        assert done == 1

    def test_first_chunk_opens_the_assistant_role(self, stream_deps):
        payloads, _done = parse_sse(
            self.stream(stream_deps, [result_event("task-1", "completed", assistant_result("OK"))])
        )

        assert payloads[0]["choices"][0]["delta"] == {"role": "assistant"}
        assert payloads[0]["object"] == "chat.completion.chunk"
        assert payloads[0]["id"] == "chatcmpl-task-1"
        assert payloads[0]["model"] == "flai-chat"
        assert payloads[0]["created"] > 0

    def test_progress_and_foreign_tokens_never_reach_the_client_as_content(self, stream_deps):
        chunks = self.stream(
            stream_deps,
            [
                progress_event("task-1", "reasoning_thinking"),
                progress_event("task-1", "routing"),
                token_event("other-task", "LEAK"),
                token_event("task-1", "safe"),
                result_event("task-1", "completed", assistant_result("safe")),
            ],
        )

        payloads, _done = parse_sse(chunks)
        assert "".join(deltas(payloads)) == "safe"
        assert "LEAK" not in "".join(chunks)

    def test_finish_reason_chunk_closes_the_stream_exactly_once(self, stream_deps):
        payloads, done = parse_sse(
            self.stream(stream_deps, [result_event("task-1", "completed", assistant_result("OK"))])
        )

        finished = [p for p in payloads if p["choices"] and p["choices"][0].get("finish_reason")]
        assert len(finished) == 1
        assert finished[0]["choices"][0]["finish_reason"] == "stop"
        assert done == 1

    def test_usage_chunk_is_emitted_only_when_requested(self, stream_deps):
        base = [result_event("task-1", "completed", assistant_result("OK", prompt_tokens=11, completion_tokens=3))]

        without, _ = parse_sse(self.stream(stream_deps, base, include_usage=False))
        with_usage, _ = parse_sse(self.stream(stream_deps, base, include_usage=True))

        assert all("usage" not in p for p in without)
        usage_chunks = [p for p in with_usage if "usage" in p]
        assert len(usage_chunks) == 1
        assert usage_chunks[0]["choices"] == []
        assert usage_chunks[0]["usage"] == {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}

    def test_requeued_phase_is_followed_without_finishing_early(self, stream_deps):
        app = Flask(__name__)
        app.config["REDIS_RESULT_TTL"] = 3600
        redis = stream_deps["install"](
            [
                token_event("task-1", "first-"),
                result_event("task-1", "completed", {"status": "queued", "request_id": "task-2"}),
                token_event("task-2", "second"),
                result_event("task-2", "completed", assistant_result("first-second")),
            ]
        )["redis"]
        redis.hset(
            "api:task:task-1",
            mapping={"login": "alice", "session_id": "s1", "endpoint": "/v1/chat/completions", "created_at": "1"},
        )
        with app.app_context():
            chunks = list(stream_chat("alice", "task-1", "s1", timeout_s=30.0))

        payloads, done = parse_sse(chunks)
        assert "".join(deltas(payloads)) == "first-second"
        assert done == 1
        finished_ids = [p["id"] for p in payloads if p["choices"] and p["choices"][0].get("finish_reason")]
        assert finished_ids == ["chatcmpl-task-2"]

    def test_tokens_never_seen_on_the_bus_are_reconciled_from_the_final_result(self, stream_deps):
        payloads, _done = parse_sse(
            self.stream(
                stream_deps,
                [
                    token_event("task-1", "Hel"),
                    result_event("task-1", "completed", assistant_result("Hello there")),
                ],
            )
        )

        assert "".join(deltas(payloads)) == "Hello there"

    def test_stream_without_tokens_emits_the_whole_answer(self, stream_deps):
        payloads, _done = parse_sse(
            self.stream(stream_deps, [result_event("task-1", "completed", assistant_result("whole answer"))])
        )

        assert "".join(deltas(payloads)) == "whole answer"

    def test_queue_error_becomes_a_localized_sse_error_then_done(self, stream_deps):
        chunks = self.stream(
            stream_deps,
            [result_event("task-1", "error", {"error": "⚠️ GPU out of memory", "session_id": "s1"})],
        )

        payloads, done = parse_sse(chunks)
        errors = [p["error"] for p in payloads if "error" in p]
        assert errors == [
            {"message": "⚠️ GPU out of memory", "type": "server_error", "param": None, "code": "task_failed"}
        ]
        assert done == 1

    def test_completed_handler_error_without_response_becomes_sse_error(self, stream_deps):
        chunks = self.stream(
            stream_deps,
            [
                result_event(
                    "task-1",
                    "completed",
                    {"is_error": True, "error": "private raw worker error"},
                )
            ],
        )
        payloads, done = parse_sse(chunks)
        errors = [payload["error"] for payload in payloads if "error" in payload]
        assert errors == [
            {"message": "Task task-1 failed", "type": "server_error", "param": None, "code": "task_failed"}
        ]
        assert "private raw worker error" not in "".join(chunks)
        assert done == 1

    def test_raw_queue_exception_is_not_exposed_in_sse(self, stream_deps):
        chunks = self.stream(
            stream_deps,
            [result_event("task-1", "error", {"error": "private traceback at /srv/flai", "session_id": "s1"})],
        )
        body = "".join(chunks)
        assert "private traceback" not in body
        assert "/srv/flai" not in body
        assert '"message": "Task task-1 failed"' in body

    def test_stalled_stream_times_out_with_an_error_chunk(self, stream_deps):
        stream_deps["clock"] = FakeClock(values=[0.0, 100.0, 200.0])

        chunks = self.stream(stream_deps, [token_event("task-1", "partial")], timeout_s=10.0)

        payloads, done = parse_sse(chunks)
        assert payloads[-1]["error"]["code"] == "task_timeout"
        assert done == 1

    def test_idle_stream_emits_heartbeat_comments(self, stream_deps):
        stream_deps["clock"] = FakeClock(step=20.0)

        chunks = self.stream(stream_deps, [], timeout_s=3600.0)

        assert any(chunk.startswith(": ping") for chunk in chunks)

    def test_consumer_disconnect_closes_pubsub_without_cancelling_the_task(self, stream_deps):
        stream_deps["install"]([token_event("task-1", "hi")])
        generator = stream_chat("alice", "task-1", "s1", timeout_s=30.0)

        assert next(generator)
        generator.close()

        assert stream_deps["redis"].pubsub_client.unsubscribed is True
        assert stream_deps["queue"].cancelled == []

    def test_recovery_reads_the_stored_result_when_the_event_is_missed(self, stream_deps):
        stream_deps["queue"].results["task-1"] = {"status": "completed", "result": assistant_result("from redis")}
        chunks = self.stream(stream_deps, [])

        payloads, done = parse_sse(chunks)
        assert "".join(deltas(payloads)) == "from redis"
        assert done == 1


@pytest.mark.unit
class TestTerminalResolution:
    """A handler failure must surface as an API error, not as empty content."""

    def resolve(self, status, result):
        return _resolve_terminal("task-1", {"status": status, "result": result})

    def test_handler_error_without_a_response_raises(self):
        with pytest.raises(ApiTaskError) as excinfo:
            self.resolve("completed", {"error": "⚠️ Failed to compute embeddings", "is_error": True})

        assert str(excinfo.value.msgid) == "⚠️ Failed to compute embeddings"

    def test_chat_error_message_is_returned_as_a_normal_answer(self):
        result = self.resolve("completed", {"response": "⚠️ Model unavailable", "is_error": True})

        assert result["response"] == "⚠️ Model unavailable"

    def test_task_level_error_envelope_raises(self):
        with pytest.raises(ApiTaskError):
            self.resolve("error", {"error": "⚠️ GPU out of memory"})

    def test_queue_error_envelope_is_used_when_inner_result_has_no_error(self):
        with pytest.raises(ApiTaskError) as excinfo:
            _resolve_terminal(
                "task-1",
                {
                    "status": "error",
                    "error": "⚠️ Request cancelled - too long in queue",
                    "result": {"session_id": "api"},
                },
            )

        assert excinfo.value.localized(lambda msg: msg) == "⚠️ Request cancelled - too long in queue"

    def test_missing_result_raises(self):
        with pytest.raises(ApiTaskError):
            self.resolve("completed", None)

    def test_requeue_without_a_follow_up_raises(self):
        with pytest.raises(ApiTaskError):
            self.resolve("completed", {"status": "queued", "request_id": "task-2"})


@pytest.mark.unit
class TestEmbeddingsBridge:
    def test_enqueue_posts_an_api_embedding_task_without_a_session(self, bridge_deps):
        api_user = {"login": "alice", "service_class": 3, "language": "de"}

        task_id = enqueue_embeddings(api_user, ["one", "two"])

        assert task_id == "task-1"
        login, session_id, request_data, user_class, lang = bridge_deps["queue"].added[0]
        assert login == "alice"
        assert session_id == "api"
        assert user_class == 3
        assert lang == "de"
        assert request_data["type"] == "api_embedding"
        assert request_data["input"] == ["one", "two"]
        assert bridge_deps["saved"] == []

    def test_enqueue_posts_api_transcription_without_a_chat_session(self, bridge_deps):
        api_user = {"login": "alice", "service_class": 3, "language": "de"}

        task_id = enqueue_transcription(api_user, "QUJD", "audio/wav", "recording.wav")

        assert task_id == "task-1"
        login, session_id, request_data, user_class, lang = bridge_deps["queue"].added[0]
        assert (login, session_id, user_class, lang) == ("alice", "api", 3, "de")
        assert request_data == {
            "type": "api_transcribe",
            "file_data": "QUJD",
            "file_type": "audio/wav",
            "file_name": "recording.wav",
            "stream": False,
        }
        assert bridge_deps["saved"] == []

    def test_transcription_language_can_override_the_account(self, bridge_deps):
        enqueue_transcription({"login": "alice", "language": "de"}, "QUJD", "audio/wav", "a.wav", "en")

        assert bridge_deps["queue"].added[0][4] == "en"

    def test_serialize_returns_indexed_float_vectors(self):
        body = serialize_embeddings({"embeddings": [[0.1, 0.2], [0.3]]})

        assert body == {
            "object": "list",
            "data": [
                {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
                {"object": "embedding", "index": 1, "embedding": [0.3]},
            ],
            "model": "flai-embeddings",
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    def test_base64_vectors_round_trip_to_float32(self):
        body = serialize_embeddings({"embeddings": [[0.5, -0.25]]}, "base64")

        decoded = struct.unpack("<2f", base64.b64decode(body["data"][0]["embedding"]))
        assert list(decoded) == pytest.approx([0.5, -0.25])

    @pytest.mark.parametrize("result", [{}, {"embeddings": []}, {"embeddings": None}, {"embeddings": "nope"}])
    def test_missing_vectors_raise_an_api_error(self, result):
        with pytest.raises(ApiTaskError):
            serialize_embeddings(result)
