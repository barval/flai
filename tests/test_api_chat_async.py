"""Contract tests for `POST /v1/flai/chat/async`.

The endpoint is fire-and-poll: it enqueues exactly the same web-shaped chat
task as `/v1/chat/completions` and answers `202` with a task id. Generation
happens on the serialized GPU queue, never inside the HTTP request, and the
answer is read back by polling `GET /v1/flai/tasks/{id}`.
"""

from unittest.mock import Mock

import pytest

from app.api_tokens import create_api_token
from app.userdb import create_user

ASYNC_CHAT = "/v1/flai/chat/async"
SESSION_ID = "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


@pytest.fixture
def api_token():
    create_user(login="apiowner", password="pw-apiowner-123", name="API Owner", language="en")
    token, _ = create_api_token("apiowner", name="test")
    return token


@pytest.fixture
def other_token():
    create_user(login="intruder", password="pw-intruder-123456", name="Intruder", language="en")
    token, _ = create_api_token("intruder", name="test")
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def queue_stub(test_app, monkeypatch):
    """Stub queue enqueue so the route never waits for a real model."""
    add_request = Mock(return_value=("async-task-1", {"position": 2, "queue_type": "fast"}))
    monkeypatch.setattr(test_app.request_queue, "add_request", add_request)
    return add_request


@pytest.fixture
def enqueue_chat_stub(monkeypatch):
    """Replace the bridge enqueue so no message is persisted in route tests."""
    stub = Mock(return_value=("async-task-1", {"position": 2, "queue_type": "fast"}))
    monkeypatch.setattr("app.routes.api_v1.enqueue_chat", stub)
    return stub


@pytest.fixture
def session_stub(monkeypatch):
    """Pin the resolved session id so route assertions stay deterministic."""
    monkeypatch.setattr(
        "app.routes.api_v1.resolve_api_session",
        lambda api_user, session_id=None, client_user=None: SESSION_ID,
    )


def post_async(client, token, **payload):
    return client.post(ASYNC_CHAT, json=payload, headers=bearer(token))


def test_returns_202_with_a_poll_url(client, api_token, enqueue_chat_stub):
    response = post_async(client, api_token, messages=[{"role": "user", "content": "Hi"}])

    assert response.status_code == 202
    body = response.get_json()
    assert body["id"] == "async-task-1"
    assert body["object"] == "flai.task"
    assert body["status"] == "queued"
    assert body["poll_url"] == "/v1/flai/tasks/async-task-1"


def test_enqueues_the_persisted_web_chat_task(client, api_token, enqueue_chat_stub, session_stub):
    post_async(client, api_token, messages=[{"role": "user", "content": "Hello there"}])

    api_user, session_id, text, images = enqueue_chat_stub.call_args[0]
    assert api_user["login"] == "apiowner"
    assert session_id == SESSION_ID
    assert text == "Hello there"
    assert images == []


def test_registers_the_task_for_its_owner(client, api_token, enqueue_chat_stub, session_stub, task_redis):
    response = post_async(client, api_token, messages=[{"role": "user", "content": "Hi"}])

    with client.application.app_context():
        from app.api_bridge import get_api_task_owner

        record = get_api_task_owner(response.get_json()["id"])
    assert record is not None
    assert record["login"] == "apiowner"
    assert record["endpoint"] == ASYNC_CHAT


def test_task_is_visible_only_to_its_owner(client, api_token, other_token, enqueue_chat_stub, session_stub, task_redis):
    task_id = post_async(client, api_token, messages=[{"role": "user", "content": "Hi"}]).get_json()["id"]

    owner_view = client.get(f"/v1/flai/tasks/{task_id}", headers=bearer(api_token))
    assert owner_view.status_code == 200

    foreign = client.get(f"/v1/flai/tasks/{task_id}", headers=bearer(other_token))
    assert foreign.status_code == 404


def test_completed_task_exposes_the_answer_text(
    client, api_token, enqueue_chat_stub, session_stub, task_redis, test_app, monkeypatch
):
    task_id = post_async(client, api_token, messages=[{"role": "user", "content": "Hi"}]).get_json()["id"]
    monkeypatch.setattr(
        test_app.request_queue,
        "check_result",
        lambda tid: {
            "status": "completed",
            "result": {
                "response": "The stored answer",
                "session_id": SESSION_ID,
                "prompt_tokens": 11,
                "completion_tokens": 4,
            },
        },
    )

    response = client.get(f"/v1/flai/tasks/{task_id}", headers=bearer(api_token))

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "completed"
    assert body["result"]["response"] == "The stored answer"
    assert body["result"]["usage"] == {"prompt_tokens": 11, "completion_tokens": 4}


def test_does_not_hold_a_synchronous_wait_slot(client, api_token, enqueue_chat_stub, monkeypatch):
    monkeypatch.setattr(
        "app.routes.api_v1._acquire_wait_slot",
        lambda: pytest.fail("async chat must not acquire a synchronous wait slot"),
    )

    response = post_async(client, api_token, messages=[{"role": "user", "content": "Hi"}])

    assert response.status_code == 202


def test_image_content_is_forwarded_to_the_queue(client, api_token, enqueue_chat_stub, monkeypatch):
    monkeypatch.setattr(
        "app.utils.resize_image_if_needed", lambda data, ftype, name, size: (data, ftype, name, False, None, None)
    )

    post_async(
        client,
        api_token,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ],
    )

    images = enqueue_chat_stub.call_args[0][3]
    assert len(images) == 1
    assert images[0]["file_type"] == "image/png"


def test_ignores_model_stream_and_tools_fields(client, api_token, enqueue_chat_stub):
    response = post_async(
        client,
        api_token,
        model="some-unknown-model",
        stream=True,
        tools=[{"type": "function"}],
        messages=[{"role": "user", "content": "Hi"}],
    )

    assert response.status_code == 202
    assert response.mimetype == "application/json"


def test_user_field_selects_a_persistent_conversation(client, api_token, enqueue_chat_stub):
    response = post_async(client, api_token, user="client-42", messages=[{"role": "user", "content": "Hi"}])

    assert response.status_code == 202


def test_explicit_session_id_is_forwarded(client, api_token, enqueue_chat_stub, monkeypatch):
    resolved = []
    monkeypatch.setattr(
        "app.routes.api_v1.resolve_api_session",
        lambda api_user, session_id=None, client_user=None: resolved.append((session_id, client_user)) or SESSION_ID,
    )

    post_async(client, api_token, metadata={"session_id": SESSION_ID}, messages=[{"role": "user", "content": "Hi"}])

    assert resolved == [(SESSION_ID, None)]


def test_foreign_session_id_returns_404_without_enqueueing(client, api_token, enqueue_chat_stub, test_app):
    from app import db

    create_user(login="otheruser", password="pw-otheruser-123456", name="Other", language="en")
    with test_app.app_context():
        foreign = db.create_session("otheruser", title="Foreign", lang="en")

    response = post_async(
        client, api_token, metadata={"session_id": foreign}, messages=[{"role": "user", "content": "Hi"}]
    )

    assert response.status_code == 404
    enqueue_chat_stub.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"messages": []},
        {"messages": [{"role": "user", "content": "  "}]},
        {"messages": [{"role": "user", "content": "Hi"}], "user": 7},
        {"messages": [{"role": "user", "content": "Hi"}], "metadata": {"session_id": 7}},
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x/i.png"}}]}]},
    ],
)
def test_malformed_requests_return_400_without_enqueueing(client, api_token, enqueue_chat_stub, payload):
    response = post_async(client, api_token, **payload)

    assert response.status_code == 400
    assert response.mimetype == "application/json"
    enqueue_chat_stub.assert_not_called()


def test_non_object_body_returns_400(client, api_token, enqueue_chat_stub):
    response = client.post(ASYNC_CHAT, data="[1, 2, 3]", content_type="application/json", headers=bearer(api_token))

    assert response.status_code == 400
    enqueue_chat_stub.assert_not_called()


def test_unauthorized_request_never_enqueues(client, enqueue_chat_stub):
    response = client.post(ASYNC_CHAT, json={"messages": [{"role": "user", "content": "Hi"}]})

    assert response.status_code == 401
    enqueue_chat_stub.assert_not_called()


def test_never_sets_a_cookie(client, api_token, enqueue_chat_stub):
    response = post_async(client, api_token, messages=[{"role": "user", "content": "Hi"}])

    assert response.headers.get("Set-Cookie") is None


def test_get_is_not_allowed(client, api_token):
    assert client.get(ASYNC_CHAT, headers=bearer(api_token)).status_code == 405
