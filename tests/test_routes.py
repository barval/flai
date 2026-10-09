# tests/test_routes.py
"""Integration tests for main routes."""

import json

import pytest


@pytest.mark.integration
class TestAuthRoutes:
    """Test authentication routes."""

    def test_login_page(self, client):
        """Test login page loads."""
        response = client.get("/login")
        assert response.status_code == 200

    def test_login_success(self, client, test_app):
        """Test successful login."""
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login

            if not get_user_by_login("logintest"):
                create_user("logintest", "pass123", "Login Test")

        response = client.post("/login", data={"login": "logintest", "password": "pass123"}, follow_redirects=True)

        assert response.status_code == 200

    def test_login_failure(self, client):
        """Test failed login."""
        response = client.post("/login", data={"login": "nonexistent", "password": "wrongpass"})

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Invalid login or password" in html or "error" in html.lower()

    def test_logout(self, client, test_app):
        """Test logout."""
        # Login first
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login

            if not get_user_by_login("logouttest"):
                create_user("logouttest", "pass123", "Logout Test")

        client.post("/login", data={"login": "logouttest", "password": "pass123"})

        # Logout
        response = client.post("/logout", follow_redirects=True)
        assert response.status_code == 200


@pytest.mark.integration
class TestChatRoutes:
    """Test chat routes."""

    def test_chat_route_requires_auth(self, client):
        """Test that chat route requires authentication."""
        response = client.get("/chat")
        assert response.status_code == 302  # Redirect to login

    def test_chat_route_authenticated(self, client, test_app):
        """Test chat route with authentication."""
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login

            if not get_user_by_login("chattest"):
                create_user("chattest", "pass123", "Chat Test")

        client.post("/login", data={"login": "chattest", "password": "pass123"})

        response = client.get("/chat")
        assert response.status_code == 200

    def test_send_message_queued(self, client, test_app, mock_llamacpp_client):
        """Test that send_message queues the request."""
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login

            if not get_user_by_login("msgtest"):
                create_user("msgtest", "pass123", "Message Test")

        client.post("/login", data={"login": "msgtest", "password": "pass123"})

        # Create a session first
        response = client.post("/api/sessions/new")
        response.get_json()["id"]  # session_id - used to create the session

        # Send message
        response = client.post(
            "/api/send_message", data=json.dumps({"message": "Hello"}), content_type="application/json"
        )

        assert response.status_code == 200
        data = response.get_json()
        assert "request_id" in data or "status" in data


@pytest.mark.integration
class TestNewSessionMarksPreviousVisited:
    """Regression: creating a new session must mark the PREVIOUS session as
    visited, exactly like switching sessions does. Without this the leaving
    session keeps its server-side unread_count after the last reply, so the
    envelope reappears on a session that was already read."""

    def test_new_session_marks_previous_visited(self, client, test_app, monkeypatch):
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login

            if not get_user_by_login("newsess"):
                create_user("newsess", "pass123", "New Session")

        client.post("/login", data={"login": "newsess", "password": "pass123"})

        # Create the first session — this sets current_session in the browser session.
        response = client.post("/api/sessions/new")
        assert response.status_code == 200
        first_id = response.get_json()["id"]

        # Spy on update_session_visit: keep the real implementation running.
        import app.db as app_db

        calls: list[tuple] = []

        orig = app_db.update_session_visit

        def spy(user_id, session_id):
            calls.append((user_id, session_id))
            return orig(user_id, session_id)

        monkeypatch.setattr(app_db, "update_session_visit", spy)

        # Create a second session — the previous one must be marked visited.
        response = client.post("/api/sessions/new")
        assert response.status_code == 200
        second_id = response.get_json()["id"]
        assert second_id != first_id

        assert ("newsess", first_id) in calls, (
            "api_new_session did not mark the previous session as visited — "
            "regression: unread envelope reappears on a read session"
        )
