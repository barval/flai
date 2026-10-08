"""Session and history endpoints under `/v1/flai/sessions`.

API clients can list and create their own sessions and read message history.
Every lookup is owner-scoped against `g.api_user["login"]` and responses never
set a cookie, because `/v1` identity is request-local Bearer identity.
"""

import pytest

from app import db
from app.api_tokens import create_api_token
from app.userdb import create_user

SESSIONS = "/v1/flai/sessions"


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
        return db.create_session("apiuser", title="History", lang="ru")


@pytest.fixture
def foreign_session(test_app):
    create_user(login="otheruser", password="pw-otheruser-123456", name="Other", language="en")
    with test_app.app_context():
        return db.create_session("otheruser", title="Foreign", lang="en")


def save_messages(test_app, session_id, count):
    with test_app.app_context():
        for index in range(count):
            db.save_message(session_id, "user", f"question {index}")


def test_sessions_list_returns_caller_sessions(client, api_user, test_app, foreign_session, owned_session):
    response = client.get(SESSIONS, headers=bearer(api_user))
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "list"
    ids = [entry["id"] for entry in body["data"]]
    assert owned_session in ids
    assert foreign_session not in ids
    entry = next(item for item in body["data"] if item["id"] == owned_session)
    assert entry["title"] == "History"
    assert "created_at" in entry


def test_create_session_uses_api_identity(client, api_user, test_app):
    created = client.post(SESSIONS, json={"title": "API created"}, headers=bearer(api_user))
    assert created.status_code == 200
    body = created.get_json()
    assert body["title"] == "API created"
    with test_app.app_context():
        from app.utils import validate_session_ownership

        owner_row = validate_session_ownership(body["id"], "apiuser")
    assert owner_row is True

    default = client.post(SESSIONS, json={}, headers=bearer(api_user))
    assert default.status_code == 200
    assert default.get_json()["title"]


def test_create_session_returns_created_at(client, api_user):
    created = client.post(SESSIONS, json={"title": "Stamped"}, headers=bearer(api_user))
    assert created.status_code == 200
    assert created.get_json()["created_at"]


def test_messages_owner_scoped_pagination(client, api_user, test_app, owned_session, foreign_session):
    save_messages(test_app, owned_session, 3)

    first_page = client.get(
        f"{SESSIONS}/{owned_session}/messages",
        query_string={"limit": 2, "offset": 0},
        headers=bearer(api_user),
    )
    assert first_page.status_code == 200
    body = first_page.get_json()
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["messages"]) == 2
    assert body["has_more"] is True
    assert body["messages"][0]["content"] == "question 0"

    second_page = client.get(
        f"{SESSIONS}/{owned_session}/messages",
        query_string={"limit": 2, "offset": 2},
        headers=bearer(api_user),
    )
    assert second_page.get_json()["has_more"] is False
    assert len(second_page.get_json()["messages"]) == 1


def test_messages_limit_is_clamped(client, api_user, test_app, owned_session):
    save_messages(test_app, owned_session, 1)
    response = client.get(
        f"{SESSIONS}/{owned_session}/messages",
        query_string={"limit": 5000, "offset": -10},
        headers=bearer(api_user),
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["limit"] == 200
    assert body["offset"] == 0


@pytest.mark.parametrize("requested", [0, -5])
def test_messages_limit_lower_bound_is_clamped(client, api_user, test_app, owned_session, requested):
    save_messages(test_app, owned_session, 1)
    response = client.get(
        f"{SESSIONS}/{owned_session}/messages",
        query_string={"limit": requested},
        headers=bearer(api_user),
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["limit"] == 1
    assert len(body["messages"]) == 1


def test_foreign_session_is_404_everywhere(client, api_user, test_app, foreign_session):
    listed = client.get(f"{SESSIONS}/{foreign_session}/messages", headers=bearer(api_user))
    assert listed.status_code == 404
    unknown = client.get(f"{SESSIONS}/unknown-session/messages", headers=bearer(api_user))
    assert unknown.status_code == 404


def test_api_responses_never_set_cookie(client, api_user, test_app, owned_session):
    checked = [
        client.get(SESSIONS, headers=bearer(api_user)),
        client.post(SESSIONS, json={}, headers=bearer(api_user)),
        client.get(f"{SESSIONS}/{owned_session}/messages", headers=bearer(api_user)),
    ]
    for response in checked:
        assert response.headers.get("Set-Cookie") is None
