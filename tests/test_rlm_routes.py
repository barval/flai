# tests/test_rlm_routes.py
"""Tests for RLM deep-analysis routes."""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def authenticated_client(client, test_app):
    """Create authenticated client."""
    with test_app.app_context():
        from app.userdb import create_user, get_user_by_login

        if not get_user_by_login("rlmtest"):
            create_user("rlmtest", "pass123", "RLM Test User")

    client.post("/login", data={"login": "rlmtest", "password": "pass123"})
    return client


@pytest.mark.unit
def test_analyze_requires_login(client):
    response = client.post("/api/rlm/analyze", json={"session_id": "s", "doc_ids": ["d1"], "question": "q"})
    assert response.status_code == 401
    assert response.get_json()["error"].startswith("⚠️ ")


@pytest.mark.unit
def test_analyze_rejects_empty_docs(authenticated_client):
    response = authenticated_client.post("/api/rlm/analyze", json={"session_id": "s", "doc_ids": [], "question": "q"})
    assert response.status_code == 400
    assert response.get_json()["error"].startswith("⚠️ ")


@pytest.mark.unit
def test_analyze_happy_path(authenticated_client, test_app):
    mock_queue = MagicMock()
    mock_queue.add_rlm_task.return_value = ("task-1", {"position": 2})
    test_app.request_queue = mock_queue

    with (
        patch("app.routes.rlm.validate_session_ownership", return_value=True),
        patch("app.routes.rlm.get_user_documents", return_value=[{"id": "d1"}]),
    ):
        response = authenticated_client.post(
            "/api/rlm/analyze", json={"session_id": "s1", "doc_ids": ["d1"], "question": "q"}
        )

    assert response.status_code == 202
    data = response.get_json()
    assert data["task_id"] == "task-1"
    assert data["position"] == 2
    mock_queue.add_rlm_task.assert_called_once_with(
        "rlmtest", "s1", ["d1"], "q", lang="ru", image_data=None, image_type=None, image_name=None
    )


@pytest.mark.unit
def test_analyze_persists_user_message(authenticated_client, test_app):
    mock_queue = MagicMock()
    mock_queue.add_rlm_task.return_value = ("task-2", {"position": 0})
    test_app.request_queue = mock_queue

    with (
        patch("app.routes.rlm.validate_session_ownership", return_value=True),
        patch("app.routes.rlm.get_user_documents", return_value=[{"id": "d1"}]),
        patch("app.routes.rlm.save_message") as mock_save,
    ):
        mock_save.return_value = "msg-2"
        response = authenticated_client.post(
            "/api/rlm/analyze",
            json={"session_id": "s1", "doc_ids": ["d1"], "question": "What is the budget?"},
        )

    assert response.status_code == 202
    data = response.get_json()
    assert data["task_id"] == "task-2"
    assert data["position"] == 0
    mock_save.assert_called_once()
    session_id, role, content = mock_save.call_args.args[:3]
    assert session_id == "s1"
    assert role == "user"
    assert json.loads(content) == [{"type": "text", "text": "What is the budget?"}]


@pytest.mark.unit
def test_analyze_queues_task_before_saving_message(authenticated_client, test_app):
    mock_queue = MagicMock()
    mock_queue.add_rlm_task.return_value = ("task-3", {"position": 1})
    test_app.request_queue = mock_queue

    with (
        patch("app.routes.rlm.validate_session_ownership", return_value=True),
        patch("app.routes.rlm.get_user_documents", return_value=[{"id": "d1"}]),
        patch("app.routes.rlm.save_message") as mock_save,
    ):
        mock_save.return_value = "msg-3"
        response = authenticated_client.post(
            "/api/rlm/analyze", json={"session_id": "s1", "doc_ids": ["d1"], "question": "q"}
        )

    assert response.status_code == 202
    mock_queue.add_rlm_task.assert_called()
    mock_save.assert_called()
    assert mock_queue.add_rlm_task.call_count == 1
    assert mock_save.call_count == 1


@pytest.mark.unit
def test_analyze_rejects_foreign_session(authenticated_client, test_app):
    mock_queue = MagicMock()
    test_app.request_queue = mock_queue

    with patch("app.routes.rlm.validate_session_ownership", return_value=False):
        response = authenticated_client.post(
            "/api/rlm/analyze", json={"session_id": "foreign-session", "doc_ids": ["d1"], "question": "q"}
        )

    assert response.status_code == 403
    assert response.get_json()["error"].startswith("⚠️ ")
    mock_queue.add_rlm_task.assert_not_called()


@pytest.mark.unit
def test_analyze_rejects_foreign_document(authenticated_client, test_app):
    mock_queue = MagicMock()
    test_app.request_queue = mock_queue

    with (
        patch("app.routes.rlm.validate_session_ownership", return_value=True),
        patch("app.routes.rlm.get_user_documents", return_value=[{"id": "other"}]),
    ):
        response = authenticated_client.post(
            "/api/rlm/analyze", json={"session_id": "s1", "doc_ids": ["d1"], "question": "q"}
        )

    assert response.status_code == 403
    assert response.get_json()["error"].startswith("⚠️ ")
    mock_queue.add_rlm_task.assert_not_called()
