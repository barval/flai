"""`POST /v1/flai/rlm` — owner-scoped deep analysis through the public API.

The endpoint validates RLM availability, session/document ownership and the
uploaded image before enqueueing through the existing `add_rlm_task` queue
method; the returned task id is registered in the Phase 3 owner index so the
standard `/v1/flai/tasks` polling flow works.
"""

import base64
import io
import json
from unittest.mock import Mock

import pytest

from app import db
from app.api_tokens import create_api_token
from app.userdb import create_user

RLM = "/v1/flai/rlm"
ME = "/v1/flai/me"
TASKS = "/v1/flai/tasks"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_user():
    create_user(login="apiuser", password="pw-apiuser-123456", name="API User", language="ru")
    token, _ = create_api_token("apiuser", name="test")
    return token


@pytest.fixture
def client(test_app, api_user):
    return test_app.test_client()


@pytest.fixture
def owned_session(test_app):
    with test_app.app_context():
        return db.create_session("apiuser", title="RLM session", lang="ru")


@pytest.fixture
def foreign_session(test_app):
    create_user(login="otheruser", password="pw-otheruser-123456", name="Other", language="en")
    with test_app.app_context():
        return db.create_session("otheruser", title="Foreign", lang="en")


@pytest.fixture
def enqueue_rlm(test_app, monkeypatch):
    mock = Mock(return_value=("rlm-task-1", {"position": 0}))
    monkeypatch.setattr(test_app.request_queue, "add_rlm_task", mock)
    return mock


def save_doc(test_app, user_id, doc_id):
    with test_app.app_context():
        db.save_document(user_id, doc_id, "note.txt", 12, ".txt", f"{user_id}/{doc_id}.txt")


def png_bytes():
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (8, 8), color="blue").save(output, format="PNG")
    return output.getvalue()


def post_rlm(client, token, **payload):
    return client.post(RLM, json=payload, headers=bearer(token))


def test_rlm_disabled_returns_403(client, api_user, test_app, owned_session):
    test_app.config["RLM_ENABLED"] = False
    response = post_rlm(
        client,
        api_user,
        session_id=owned_session,
        doc_ids=["doc-1"],
        question="What does it say?",
    )
    assert response.status_code == 403


def test_rlm_requires_question_session_and_corpus(client, api_user, test_app, owned_session):
    test_app.config["RLM_ENABLED"] = True
    missing_question = post_rlm(client, api_user, session_id=owned_session, doc_ids=["doc-1"], question="  ")
    assert missing_question.status_code == 400
    missing_session = post_rlm(client, api_user, doc_ids=["doc-1"], question="Summarize")
    assert missing_session.status_code == 400
    missing_corpus = post_rlm(
        client,
        api_user,
        session_id=owned_session,
        doc_ids=[],
        question="Summarize",
    )
    assert missing_corpus.status_code == 400


def test_rlm_rejects_non_string_question(client, api_user, test_app, owned_session):
    test_app.config["RLM_ENABLED"] = True
    response = post_rlm(client, api_user, session_id=owned_session, doc_ids=["doc-1"], question=42)
    assert response.status_code == 400


def test_rlm_rejects_foreign_session(client, api_user, test_app, foreign_session, enqueue_rlm):
    test_app.config["RLM_ENABLED"] = True
    save_doc(test_app, "apiuser", "doc-mine")
    response = post_rlm(
        client,
        api_user,
        session_id=foreign_session,
        doc_ids=["doc-mine"],
        question="Summarize",
    )
    assert response.status_code == 404
    enqueue_rlm.assert_not_called()


def test_rlm_rejects_foreign_document(client, api_user, test_app, owned_session, enqueue_rlm):
    test_app.config["RLM_ENABLED"] = True
    save_doc(test_app, "otheruser", "doc-foreign")
    response = post_rlm(
        client,
        api_user,
        session_id=owned_session,
        doc_ids=["doc-foreign"],
        question="Summarize",
    )
    assert response.status_code == 404
    enqueue_rlm.assert_not_called()


def test_rlm_enqueues_with_owned_docs_and_registers_task(
    client, api_user, test_app, owned_session, enqueue_rlm, task_redis
):
    test_app.config["RLM_ENABLED"] = True
    save_doc(test_app, "apiuser", "doc-mine")
    save_doc(test_app, "apiuser", "doc-second")
    save_doc(test_app, "otheruser", "doc-foreign")

    response = post_rlm(
        client,
        api_user,
        session_id=owned_session,
        doc_ids=["doc-mine", "doc-second"],
        question="Compare the reports",
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["task_id"] == "rlm-task-1"
    assert body["position"] == 0
    assert isinstance(body["user_message_id"], int)

    assert enqueue_rlm.call_count == 1
    args = enqueue_rlm.call_args.args
    assert args[0] == "apiuser"
    assert args[1] == owned_session
    assert args[2] == ["doc-mine", "doc-second"]
    assert args[3] == "Compare the reports"
    assert enqueue_rlm.call_args.kwargs["lang"] == "ru"

    polled = client.get(f"{TASKS}/rlm-task-1", headers=bearer(api_user))
    assert polled.status_code == 200


def test_rlm_with_image_persists_and_queues(client, api_user, test_app, owned_session, enqueue_rlm):
    test_app.config["RLM_ENABLED"] = True
    save_doc(test_app, "apiuser", "doc-mine")
    data = {
        "file": (io.BytesIO(png_bytes()), "scan.png", "image/png"),
        "session_id": owned_session,
        "doc_ids": '["doc-mine"]',
        "question": "Read the diagram",
    }
    response = client.post(RLM, data=data, headers=bearer(api_user), content_type="multipart/form-data")
    assert response.status_code == 202

    image_data = enqueue_rlm.call_args.kwargs["image_data"]
    assert image_data
    assert enqueue_rlm.call_args.kwargs["image_type"] == "image/png"
    assert enqueue_rlm.call_args.kwargs["image_name"] == "scan.png"

    with test_app.app_context():
        messages = db.get_session_messages(owned_session, limit=5)
    saved = json.loads(messages[0]["content"])
    assert saved[0] == {"type": "text", "text": "Read the diagram"}
    assert saved[1]["type"] == "image"
    assert saved[1]["file_name"] == "scan.png"


def test_rlm_accepts_multi_image_files_list(client, api_user, test_app, owned_session, enqueue_rlm):
    """Multipart `files[]` carries up to MAX_CHAT_IMAGES images (web parity)."""
    test_app.config["RLM_ENABLED"] = True
    save_doc(test_app, "apiuser", "doc-mine")
    data = {
        "files": [
            (io.BytesIO(png_bytes()), "a.png", "image/png"),
            (io.BytesIO(png_bytes()), "b.png", "image/png"),
        ],
        "session_id": owned_session,
        "doc_ids": '["doc-mine"]',
        "question": "Compare the charts",
    }
    response = client.post(RLM, data=data, headers=bearer(api_user), content_type="multipart/form-data")
    assert response.status_code == 202, response.get_json()

    kwargs = enqueue_rlm.call_args.kwargs
    assert kwargs["image_data"]  # first image keeps the legacy args
    assert [img["name"] for img in kwargs["images"]] == ["a.png", "b.png"]
    assert len(kwargs["images"]) == 2

    with test_app.app_context():
        messages = db.get_session_messages(owned_session, limit=5)
    saved = json.loads(messages[0]["content"])
    image_parts = [p for p in saved if p.get("type") == "image"]
    assert [p["file_name"] for p in image_parts] == ["a.png", "b.png"]


def test_rlm_json_images_array_builds_corpus_images(client, api_user, test_app, owned_session, enqueue_rlm):
    """JSON callers pass OpenAI-style image_url parts with inline data: URLs."""
    test_app.config["RLM_ENABLED"] = True
    save_doc(test_app, "apiuser", "doc-mine")
    png_b64 = base64.b64encode(png_bytes()).decode("ascii")
    response = post_rlm(
        client,
        api_user,
        session_id=owned_session,
        doc_ids=["doc-mine"],
        question="Read both",
        images=[
            {"image_url": {"url": f"data:image/png;base64,{png_b64}"}},
            {"image_url": {"url": f"data:image/png;base64,{png_b64}"}},
        ],
    )
    assert response.status_code == 202, response.get_json()
    kwargs = enqueue_rlm.call_args.kwargs
    assert len(kwargs["images"]) == 2
    assert kwargs["image_data"]

    with test_app.app_context():
        messages = db.get_session_messages(owned_session, limit=5)
    saved = json.loads(messages[0]["content"])
    assert len([p for p in saved if p.get("type") == "image"]) == 2


def test_rlm_json_rejects_remote_image_url(client, api_user, test_app, owned_session, enqueue_rlm):
    test_app.config["RLM_ENABLED"] = True
    save_doc(test_app, "apiuser", "doc-mine")
    response = post_rlm(
        client,
        api_user,
        session_id=owned_session,
        doc_ids=["doc-mine"],
        question="q",
        images=[{"image_url": {"url": "https://example.com/cat.png"}}],
    )
    assert response.status_code == 400
    enqueue_rlm.assert_not_called()


def test_me_reports_rlm_capability(client, api_user, test_app):
    test_app.config["RLM_ENABLED"] = True
    assert client.get(ME, headers=bearer(api_user)).get_json()["capabilities"]["rlm"] is True
    test_app.config["RLM_ENABLED"] = False
    assert client.get(ME, headers=bearer(api_user)).get_json()["capabilities"]["rlm"] is False
