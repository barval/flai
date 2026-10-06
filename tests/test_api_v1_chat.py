"""Contract tests for the OpenAI-compatible chat and embeddings endpoints."""

import base64
from unittest.mock import Mock

import pytest

from app.api_tokens import create_api_token
from app.userdb import create_user


@pytest.fixture
def api_token(test_app):
    create_user(login="apiowner", password="pw-apiowner-123", name="API Owner", language="en")
    token, _ = create_api_token("apiowner", name="test")
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def chat_stub(monkeypatch):
    """Stub the bridge so route tests never touch Redis, the queue or the DB."""
    state = {
        "session_id": "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
        "result": {
            "response": "OK",
            "session_id": "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
            "model_used": "reasoning-model",
            "is_error": False,
            "message_id": 12,
            "prompt_tokens": 5,
            "completion_tokens": 2,
        },
        "enqueued": [],
        "resolved": [],
        "raise_on_wait": None,
    }
    monkeypatch.setattr(
        "app.routes.api_v1.resolve_api_session",
        lambda api_user, session_id=None, client_user=None: (
            state["resolved"].append((api_user["login"], session_id, client_user)) or state["session_id"]
        ),
    )
    monkeypatch.setattr(
        "app.routes.api_v1.enqueue_chat",
        lambda api_user, session_id, text, images=None: (
            state["enqueued"].append((api_user["login"], session_id, text, images))
            or ("task-1", {"position": 1, "estimated_seconds": 3, "queue_type": "fast"})
        ),
    )

    def fake_wait(login, task_id, timeout_s):
        if state["raise_on_wait"]:
            raise state["raise_on_wait"]
        return state["result"]

    monkeypatch.setattr("app.routes.api_v1.wait_for_result", fake_wait)
    return state


def post_chat(client, token, payload):
    return client.post("/v1/chat/completions", json=payload, headers=bearer(token))


def response_body(client, token, payload):
    return post_chat(client, token, payload).get_data(as_text=True)


@pytest.mark.unit
class TestChatCompletions:
    def test_returns_an_openai_chat_completion(self, client, api_token, chat_stub, monkeypatch):
        class RegistryRedis:
            def __init__(self):
                self.hashes = {}
                self.sorted_sets = {}

            def hset(self, key, mapping):
                self.hashes.setdefault(key, {}).update(mapping)

            def hgetall(self, key):
                return self.hashes.get(key, {})

            def zadd(self, key, mapping):
                self.sorted_sets.setdefault(key, {}).update(mapping)

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
                self.hashes[task_key] = {
                    "login": login,
                    "session_id": session_id,
                    "endpoint": endpoint,
                    "created_at": str(created_at),
                }
                self.zadd(index_key, {task_id: float(created_at)})
                return 1

            def expire(self, key, ttl):
                return True

            def close(self):
                return None

        registry = RegistryRedis()
        monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: registry)
        response = post_chat(client, api_token, {"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]})

        assert response.status_code == 200
        body = response.get_json()
        assert body["object"] == "chat.completion"
        assert body["id"] == "chatcmpl-task-1"
        assert body["model"] == "flai-chat"
        assert body["choices"][0]["message"] == {"role": "assistant", "content": "OK"}
        assert body["choices"][0]["finish_reason"] == "stop"
        assert body["usage"] == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
        from app.api_bridge import get_api_task_owner

        with client.application.app_context():
            assert get_api_task_owner("task-1")["login"] == "apiowner"

    def test_arbitrary_model_name_is_accepted_and_ignored(self, client, api_token, chat_stub):
        for model in ("gpt-4o", "claude-3", "my-private-finetune", ""):
            response = post_chat(client, api_token, {"model": model, "messages": [{"role": "user", "content": "Hi"}]})
            assert response.status_code == 200
            assert response.get_json()["model"] == "flai-chat"

    def test_missing_model_key_is_accepted(self, client, api_token, chat_stub):
        response = post_chat(client, api_token, {"messages": [{"role": "user", "content": "Hi"}]})

        assert response.status_code == 200

    def test_tools_and_response_format_are_ignored(self, client, api_token, chat_stub):
        response = post_chat(
            client,
            api_token,
            {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hi"}],
                "tools": [{"type": "function", "function": {"name": "x"}}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}},
            },
        )

        assert response.status_code == 200

    def test_request_is_authenticated_as_the_key_owner(self, client, api_token, chat_stub):
        post_chat(client, api_token, {"messages": [{"role": "user", "content": "Hi"}]})

        assert chat_stub["enqueued"][0][0] == "apiowner"
        assert chat_stub["resolved"][0][0] == "apiowner"

    def test_user_field_maps_to_a_persistent_conversation(self, client, api_token, chat_stub):
        post_chat(client, api_token, {"user": "home-assistant", "messages": [{"role": "user", "content": "Hi"}]})

        assert chat_stub["resolved"][0] == ("apiowner", None, "home-assistant")

    def test_explicit_session_id_is_forwarded_for_ownership_check(self, client, api_token, chat_stub):
        session_id = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
        post_chat(
            client,
            api_token,
            {
                "user": "ignored",
                "metadata": {"session_id": session_id},
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

        assert chat_stub["resolved"][0] == ("apiowner", session_id, "ignored")

    def test_foreign_session_id_returns_openai_404(self, client, api_token, monkeypatch):
        from app.api_bridge import ApiSessionNotFoundError

        monkeypatch.setattr(
            "app.routes.api_v1.resolve_api_session",
            Mock(side_effect=ApiSessionNotFoundError("foreign")),
        )

        response = post_chat(
            client,
            api_token,
            {
                "metadata": {"session_id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"},
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

        assert response.status_code == 404
        body = response.get_json()
        assert body["error"]["code"] == "session_not_found"
        assert body["error"]["message"].startswith("⚠️ ")

    def test_response_exposes_the_session_for_continuity(self, client, api_token, chat_stub):
        response = post_chat(client, api_token, {"messages": [{"role": "user", "content": "Hi"}]})

        assert response.get_json()["flai_session_id"] == chat_stub["session_id"]

    def test_never_sets_a_cookie_or_touches_the_browser_session(self, client, api_token, chat_stub):
        response = post_chat(client, api_token, {"messages": [{"role": "user", "content": "Hi"}]})

        assert "Set-Cookie" not in response.headers
        with client.session_transaction() as browser_session:
            assert "login" not in browser_session

    def test_timeout_returns_408_with_the_running_task_id(self, client, api_token, chat_stub):
        from app.api_bridge import ApiTaskTimeoutError

        chat_stub["raise_on_wait"] = ApiTaskTimeoutError("task-1")

        response = post_chat(client, api_token, {"messages": [{"role": "user", "content": "Hi"}]})

        assert response.status_code == 408
        body = response.get_json()
        assert body["error"]["code"] == "task_timeout"
        assert "task-1" in body["error"]["message"]
        assert body["error"]["message"].startswith("⚠️ ")

    def test_queue_error_is_mapped_to_an_openai_error(self, client, api_token, chat_stub):
        from app.api_bridge import ApiTaskError

        chat_stub["raise_on_wait"] = ApiTaskError("⚠️ GPU memory unavailable")

        response = post_chat(client, api_token, {"messages": [{"role": "user", "content": "Hi"}]})

        assert response.status_code == 500
        body = response.get_json()
        assert body["error"]["type"] == "server_error"
        assert body["error"]["message"].startswith("⚠️ ")

    def test_unusable_image_url_returns_400_without_enqueueing(self, client, api_token, chat_stub):
        response = post_chat(
            client,
            api_token,
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What is this?"},
                            {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
                        ],
                    }
                ]
            },
        )

        assert response.status_code == 400
        assert response.get_json()["error"]["type"] == "invalid_request_error"
        assert response.get_json()["error"]["code"] == "invalid_request"
        assert chat_stub["enqueued"] == []

    @pytest.mark.parametrize("payload", [{}, {"messages": []}, {"messages": "hi"}, {"messages": [{"content": "x"}]}])
    def test_malformed_requests_return_400(self, client, api_token, chat_stub, payload):
        response = post_chat(client, api_token, payload)

        assert response.status_code == 400
        assert response.get_json()["error"]["type"] == "invalid_request_error"
        assert chat_stub["enqueued"] == []

    def test_multipart_text_is_forwarded_to_the_queue(self, client, api_token, chat_stub):
        post_chat(
            client,
            api_token,
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "part one"}, {"type": "text", "text": "part two"}],
                    }
                ]
            },
        )

        assert chat_stub["enqueued"][0][2] == "part one\npart two"

    def test_web_routes_keep_their_own_405_response(self, client):
        """The app-level 405 handler must not swallow web errors."""
        response = client.get("/api/send_message")

        assert response.status_code == 405
        assert not response.is_json

    def test_unauthorized_request_never_reaches_the_queue(self, client, chat_stub):
        response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "Hi"}]})

        assert response.status_code == 401
        assert chat_stub["enqueued"] == []

    def test_get_is_not_allowed(self, client, api_token):
        response = client.get("/v1/chat/completions", headers=bearer(api_token))

        assert response.status_code == 405
        body = response.get_json()
        assert body["error"]["code"] == "method_not_allowed"
        assert body["error"]["message"].startswith("⚠️ ")


@pytest.fixture
def stream_stub(chat_stub, monkeypatch):
    """Serve a scripted SSE body so route tests never block on Redis."""
    calls = []

    def fake_stream(login, task_id, session_id, include_usage=False, timeout_s=None, translate=None):
        calls.append(
            {
                "login": login,
                "task_id": task_id,
                "session_id": session_id,
                "include_usage": include_usage,
                "timeout_s": timeout_s,
                "translate": translate,
            }
        )
        yield 'data: {"id": "chatcmpl-task-1", "object": "chat.completion.chunk", "created": 1, "model": "flai-chat", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": null}]}\n\n'
        yield 'data: {"id": "chatcmpl-task-1", "object": "chat.completion.chunk", "created": 1, "model": "flai-chat", "choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": null}]}\n\n'
        yield 'data: {"id": "chatcmpl-task-1", "object": "chat.completion.chunk", "created": 1, "model": "flai-chat", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "flai_session_id": "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"}\n\n'
        if include_usage:
            yield 'data: {"id": "chatcmpl-task-1", "object": "chat.completion.chunk", "created": 1, "model": "flai-chat", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("app.routes.api_v1.stream_chat", fake_stream)
    return {"calls": calls, "chat": chat_stub}


@pytest.mark.unit
class TestChatCompletionsStreaming:
    def test_stream_true_returns_an_sse_response(self, client, api_token, stream_stub):
        response = post_chat(client, api_token, {"stream": True, "messages": [{"role": "user", "content": "Hi"}]})

        assert response.status_code == 200
        assert response.mimetype == "text/event-stream"
        body = response.get_data(as_text=True)
        assert "data: " in body
        assert body.rstrip().endswith("data: [DONE]")
        assert '"delta": {"content": "Hi"}' in body

    def test_streaming_response_is_not_buffered_by_proxies(self, client, api_token, stream_stub):
        response = post_chat(client, api_token, {"stream": True, "messages": [{"role": "user", "content": "Hi"}]})

        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["X-Accel-Buffering"] == "no"

    def test_streaming_never_sets_a_cookie(self, client, api_token, stream_stub):
        response = post_chat(client, api_token, {"stream": True, "messages": [{"role": "user", "content": "Hi"}]})

        assert "Set-Cookie" not in response.headers

    def test_include_usage_defaults_to_false(self, client, api_token, stream_stub):
        post_chat(client, api_token, {"stream": True, "messages": [{"role": "user", "content": "Hi"}]})

        assert stream_stub["calls"][0]["include_usage"] is False
        assert "usage" not in response_body(
            client, api_token, {"stream": True, "messages": [{"role": "user", "content": "Hi"}]}
        )

    def test_include_usage_true_adds_a_usage_chunk(self, client, api_token, stream_stub):
        body = response_body(
            client,
            api_token,
            {
                "stream": True,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

        assert '"usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}' in body

    def test_streaming_uses_the_same_queue_path_as_the_sync_call(self, client, api_token, stream_stub):
        post_chat(client, api_token, {"stream": True, "user": "ha", "messages": [{"role": "user", "content": "Hi"}]})

        assert stream_stub["chat"]["enqueued"][0][0] == "apiowner"
        assert stream_stub["calls"][0]["login"] == "apiowner"
        assert stream_stub["calls"][0]["task_id"] == "task-1"

    def test_streaming_timeout_comes_from_configuration(self, client, api_token, stream_stub, test_app):
        test_app.config["API_SYNC_MAX_WAIT"] = 42

        post_chat(client, api_token, {"stream": True, "messages": [{"role": "user", "content": "Hi"}]})

        assert stream_stub["calls"][0]["timeout_s"] == 42

    def test_malformed_streaming_request_still_returns_json(self, client, api_token, stream_stub):
        response = post_chat(client, api_token, {"stream": True, "messages": []})

        assert response.status_code == 400
        assert response.mimetype == "application/json"
        assert stream_stub["calls"] == []

    def test_non_boolean_stream_flag_is_rejected(self, client, api_token, stream_stub):
        response = post_chat(client, api_token, {"stream": "yes", "messages": [{"role": "user", "content": "Hi"}]})

        assert response.status_code == 400
        assert stream_stub["calls"] == []

    def test_unauthorized_streaming_request_is_rejected(self, client, stream_stub):
        response = client.post(
            "/v1/chat/completions", json={"stream": True, "messages": [{"role": "user", "content": "Hi"}]}
        )

        assert response.status_code == 401
        assert stream_stub["calls"] == []


@pytest.mark.unit
class TestEnqueueChatPersistence:
    """app/api_bridge.py: enqueue_chat must save every image to disk and write
    plain file_path parts, so API chat history opens without base64 blobs."""

    def _png(self):
        return {
            "file_data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40).decode("ascii"),
            "file_type": "image/png",
            "file_name": "a.png",
        }

    def test_enqueue_chat_saves_images_and_writes_path_parts(self, api_token, test_app):
        import json as _json
        import os as _os

        from app import db
        from app.api_bridge import enqueue_chat

        with test_app.app_context():
            session_id = db.create_session("apiowner", title="bridge persistence")
            images = [self._png(), {**self._png(), "file_name": "b.png"}]
            task_id, _info = enqueue_chat(
                {
                    "login": "apiowner",
                    "language": "en",
                    "response_style": "neutral",
                    "service_class": "standard",
                },
                session_id,
                "compare",
                images,
            )
            assert task_id

            messages = db.get_session_messages(session_id)
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert user_msgs
        parts = _json.loads(user_msgs[-1]["content"])
        image_parts = [p for p in parts if p.get("type") == "image"]
        assert [p["file_name"] for p in image_parts] == ["a.png", "b.png"]
        for p in image_parts:
            assert p.get("file_path"), "every API chat image part must carry a disk path"
            assert "file_data" not in p, "API chat image base64 must not reach history"
            assert _os.path.exists(_os.path.join(test_app.config["UPLOAD_FOLDER"], p["file_path"]))
