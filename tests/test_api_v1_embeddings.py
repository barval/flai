"""Contract tests for the OpenAI-compatible embeddings endpoint."""

from base64 import b64decode
from struct import unpack

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
def embed_stub(monkeypatch):
    """Stub the bridge so route tests never load an embedding model."""
    state = {
        "enqueued": [],
        "vectors": [[0.1, 0.2, 0.3]],
        "result": None,
        "raise_on_wait": None,
    }
    monkeypatch.setattr(
        "app.routes.api_v1.enqueue_embeddings",
        lambda api_user, texts: state["enqueued"].append((api_user["login"], texts)) or "task-9",
    )

    def fake_wait(login, task_id, timeout_s):
        if state["raise_on_wait"]:
            raise state["raise_on_wait"]
        return state["result"] or {"embeddings": state["vectors"], "model": "embedding"}

    monkeypatch.setattr("app.routes.api_v1.wait_for_result", fake_wait)
    return state


def post_embeddings(client, token, payload):
    return client.post("/v1/embeddings", json=payload, headers=bearer(token))


@pytest.mark.unit
class TestEmbeddings:
    def test_single_string_input_returns_one_vector(self, client, api_token, embed_stub):
        response = post_embeddings(client, api_token, {"model": "text-embedding-3-small", "input": "hello"})

        assert response.status_code == 200
        body = response.get_json()
        assert body["object"] == "list"
        assert body["model"] == "flai-embeddings"
        assert body["data"] == [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}]
        assert body["usage"] == {"prompt_tokens": 0, "total_tokens": 0}

    def test_list_input_keeps_order_and_indices(self, client, api_token, embed_stub):
        embed_stub["vectors"] = [[0.1], [0.2], [0.3]]

        response = post_embeddings(client, api_token, {"input": ["a", "b", "c"]})

        assert response.status_code == 200
        data = response.get_json()["data"]
        assert [item["index"] for item in data] == [0, 1, 2]
        assert [item["embedding"] for item in data] == [[0.1], [0.2], [0.3]]
        assert embed_stub["enqueued"] == [("apiowner", ["a", "b", "c"])]

    def test_base64_encoding_format_is_float32_little_endian(self, client, api_token, embed_stub):
        embed_stub["vectors"] = [[0.5, -0.25]]

        response = post_embeddings(client, api_token, {"input": "hello", "encoding_format": "base64"})

        assert response.status_code == 200
        encoded = response.get_json()["data"][0]["embedding"]
        assert isinstance(encoded, str)
        assert list(unpack("<2f", b64decode(encoded))) == pytest.approx([0.5, -0.25])

    def test_arbitrary_model_dimensions_and_user_are_ignored(self, client, api_token, embed_stub):
        response = post_embeddings(
            client,
            api_token,
            {"model": "my-model", "input": "hello", "dimensions": 256, "user": "someone"},
        )

        assert response.status_code == 200
        assert response.get_json()["model"] == "flai-embeddings"

    def test_embeddings_request_never_creates_or_touches_a_chat_session(
        self, client, api_token, embed_stub, monkeypatch
    ):
        def forbidden(*args, **kwargs):
            pytest.fail("embeddings must not touch chat sessions or messages")

        monkeypatch.setattr("app.api_bridge.create_session", forbidden)
        monkeypatch.setattr("app.api_bridge.save_message", forbidden)

        assert post_embeddings(client, api_token, {"input": "hello"}).status_code == 200

    def test_never_sets_a_cookie(self, client, api_token, embed_stub):
        assert "Set-Cookie" not in post_embeddings(client, api_token, {"input": "hello"}).headers

    def test_handler_failure_is_reported_as_a_localized_error(self, client, api_token, embed_stub):
        from app.api_bridge import ApiTaskError

        embed_stub["raise_on_wait"] = ApiTaskError("⚠️ Failed to compute embeddings")

        response = post_embeddings(client, api_token, {"input": "hello"})

        assert response.status_code == 502
        body = response.get_json()
        assert body["error"]["message"] == "⚠️ Failed to compute embeddings"
        assert body["error"]["type"] == "server_error"

    def test_timeout_returns_408(self, client, api_token, embed_stub):
        from app.api_bridge import ApiTaskTimeoutError

        embed_stub["raise_on_wait"] = ApiTaskTimeoutError("task-9")

        response = post_embeddings(client, api_token, {"input": "hello"})

        assert response.status_code == 408
        assert response.get_json()["error"]["code"] == "task_timeout"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"input": ""},
            {"input": []},
            {"input": ["ok", 5]},
            {"input": [[1, 2, 3]]},
            {"input": None},
            {"input": 42},
            {"input": "hello", "encoding_format": "int8"},
            {"input": "hello", "encoding_format": 7},
            {"input": "   "},
        ],
    )
    def test_invalid_requests_return_400(self, client, api_token, embed_stub, payload):
        response = post_embeddings(client, api_token, payload)

        assert response.status_code == 400
        assert response.get_json()["error"]["type"] == "invalid_request_error"
        assert embed_stub["enqueued"] == []

    def test_result_without_vectors_is_an_error_not_a_silent_empty_list(self, client, api_token, embed_stub):
        embed_stub["result"] = {"model": "embedding"}

        response = post_embeddings(client, api_token, {"input": "hello"})

        assert response.status_code == 502
        assert response.get_json()["error"]["message"].startswith("⚠️ ")

    def test_get_is_not_allowed(self, client, api_token):
        response = client.get("/v1/embeddings", headers=bearer(api_token))

        assert response.status_code == 405
        assert response.get_json()["error"]["code"] == "method_not_allowed"

    def test_unauthorized_request_never_reaches_the_queue(self, client, embed_stub):
        assert client.post("/v1/embeddings", json={"input": "hello"}).status_code == 401
        assert embed_stub["enqueued"] == []
