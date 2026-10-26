# tests/test_monitoring_api.py
"""Admin endpoint tests for the Monitoring tab."""

import pytest

from app.database import get_db
from tests._monitoring_fixtures import seed_monitoring_data

pytestmark = pytest.mark.requires_db


@pytest.fixture
def admin_client(client, test_app):
    """An authenticated admin client (same pattern as tests/test_admin_routes.py)."""
    with test_app.app_context():
        from app.userdb import create_user, get_user_by_login, update_password

        if get_user_by_login("admin"):
            update_password("admin", "adminpass")
        else:
            create_user("admin", "adminpass", "Admin User", is_admin=True)

    client.post("/login", data={"login": "admin", "password": "adminpass"})
    return client


def test_monitoring_requires_admin(client):
    assert client.get("/admin/api/monitoring").status_code == 403


def test_invalid_range_is_rejected(admin_client):
    assert admin_client.get("/admin/api/monitoring?range=2h").status_code == 400


def test_unknown_user_is_rejected(admin_client):
    assert admin_client.get("/admin/api/monitoring?user=ghost").status_code == 400


def test_all_users_payload_lists_selectable_users(admin_client, test_app):
    with test_app.app_context():
        seed_monitoring_data()

    payload = admin_client.get("/admin/api/monitoring?user=all&range=24h")
    assert payload.status_code == 200, payload.data
    payload = payload.get_json()

    assert "u1" in [entry["login"] for entry in payload["users"]]
    assert len(payload["buckets"]) == 24


def test_all_users_excludes_a_login_outside_the_list(admin_client, test_app):
    """The admin login is not in list_users(); its activity must not leak into All users."""
    with test_app.app_context():
        seed_monitoring_data()
        with get_db() as conn:
            conn.cursor().execute(
                "INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-admin', 'admin', 't')"
            )

    payload = admin_client.get("/admin/api/monitoring?user=all&range=24h").get_json()

    assert "admin" not in [entry["login"] for entry in payload["users"]]
    assert payload["totals"]["sessions"] == 1


def test_endpoint_works_without_redis(admin_client, test_app, monkeypatch):
    monkeypatch.setattr(
        "app.api_bridge.get_redis_client",
        lambda: (_ for _ in ()).throw(RuntimeError("no redis")),
    )
    with test_app.app_context():
        seed_monitoring_data()

    assert admin_client.get("/admin/api/monitoring?range=24h").status_code == 200


def test_single_user_payload_keeps_the_whole_selector(admin_client, test_app):
    """Choosing one user must not empty the selector down to that user."""
    with test_app.app_context():
        seed_monitoring_data()
        from app.userdb import create_user

        create_user("u2", "pw", "U2")

    payload = admin_client.get("/admin/api/monitoring?user=u1&range=24h").get_json()

    assert {entry["login"] for entry in payload["users"]} == {"u1", "u2"}


def test_admin_login_is_not_monitorable(admin_client, test_app):
    with test_app.app_context():
        seed_monitoring_data()

    assert admin_client.get("/admin/api/monitoring?user=admin").status_code == 400


def test_endpoint_closes_the_redis_client(admin_client, test_app, monkeypatch):
    class _Client:
        def __init__(self):
            self.closed = False

        def get(self, key):
            return None

        def setex(self, key, ttl, value):
            return True

        def close(self):
            self.closed = True

    holder = _Client()
    monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: holder)
    with test_app.app_context():
        seed_monitoring_data()

    assert admin_client.get("/admin/api/monitoring?range=24h").status_code == 200
    assert holder.closed is True


def test_deleted_user_disappears_from_all_users(admin_client, test_app):
    with test_app.app_context():
        from app.userdb import delete_user

        seed_monitoring_data()
        delete_user("u1")

    payload = admin_client.get("/admin/api/monitoring?user=all&range=24h").get_json()

    assert payload["users"] == []
    assert payload["totals"]["sessions"] == 0
