# tests/test_storage_limits.py
"""Per-user upload limits edited from the admin Users tab."""

from pathlib import Path

import pytest
import pytz

from app.database import get_db
from app.db import get_user_storage_limit, set_user_storage_limit

pytestmark = pytest.mark.requires_db

MB = 1024 * 1024


@pytest.fixture
def admin_client(client, test_app):
    with test_app.app_context():
        from app.userdb import create_user, get_user_by_login, update_password

        if get_user_by_login("admin"):
            update_password("admin", "adminpass")
        else:
            create_user("admin", "adminpass", "Admin User", is_admin=True)
        for login in ("limituser", "plainuser"):
            if not get_user_by_login(login):
                create_user(login=login, password=f"pw-{login}-123456", name=login)

    client.post("/login", data={"login": "admin", "password": "adminpass"})
    return client


def _cleanup() -> None:
    with get_db() as conn:
        conn.cursor().execute("DELETE FROM user_storage WHERE user_id IN ('limituser', 'plainuser')")


def test_per_user_limit_is_stored_and_cleared(test_app):
    with test_app.app_context():
        _cleanup()
        set_user_storage_limit("limituser", 42 * MB)
        assert get_user_storage_limit("limituser") == 42 * MB

        set_user_storage_limit("limituser", None)
        assert get_user_storage_limit("limituser") is None
        _cleanup()


def test_upload_quota_uses_the_individual_limit(test_app):
    from app.utils import check_upload_quota

    with test_app.app_context():
        _cleanup()
        test_app.config["MAX_UPLOAD_STORAGE_MB"] = 500
        set_user_storage_limit("limituser", 1 * MB)

        # gettext() needs a request context; production always has one.
        with test_app.test_request_context():
            blocked = check_upload_quota("limituser", 2 * MB)
            allowed = check_upload_quota("plainuser", 2 * MB)

        assert blocked is not None, "the 1 MB override must reject a 2 MB upload"
        assert allowed is None, "a user without an override keeps the global default"
        _cleanup()


def test_admin_payload_reports_the_effective_limit(admin_client, test_app):
    with test_app.app_context():
        _cleanup()
        set_user_storage_limit("limituser", 42 * MB)

    rows = {row["login"]: row for row in admin_client.get("/admin/api/users").get_json()}

    assert rows["limituser"]["storage_limit_mb"] == 42
    assert rows["limituser"]["storage_limit_custom"] is True
    assert rows["plainuser"]["storage_limit_custom"] is False
    assert rows["plainuser"]["storage_limit_mb"] == rows["plainuser"]["storage_limit_default_mb"]


def test_admin_can_set_and_reset_the_limit(admin_client, test_app):
    with test_app.app_context():
        _cleanup()

    saved = admin_client.put("/admin/api/users/limituser", json={"storage_limit_mb": 64})
    assert saved.status_code == 200
    assert saved.get_json()["storage_limit_mb"] == 64

    with test_app.app_context():
        assert get_user_storage_limit("limituser") == 64 * MB

    reset = admin_client.put("/admin/api/users/limituser", json={"storage_limit_mb": None})
    assert reset.status_code == 200
    with test_app.app_context():
        assert get_user_storage_limit("limituser") is None
        _cleanup()


def test_invalid_limit_is_rejected(admin_client):
    response = admin_client.put("/admin/api/users/limituser", json={"storage_limit_mb": "abc"})

    assert response.status_code == 400
    assert response.get_json()["error"].startswith("⚠️ ")


def test_monitoring_sums_the_individual_limits(test_app):
    from app.monitoring.queries import build_payload

    with test_app.app_context():
        _cleanup()
        test_app.config["TIMEZONE"] = pytz.UTC
        test_app.config["MAX_UPLOAD_STORAGE_MB"] = 500
        set_user_storage_limit("limituser", 100 * MB)

        payload = build_payload(["limituser", "plainuser"], "24h")

    assert payload["storage"]["limit_bytes"] == (100 + 500) * MB
    _cleanup_if_possible()


def _cleanup_if_possible() -> None:
    import contextlib

    with contextlib.suppress(Exception):
        _cleanup()


@pytest.mark.unit
def test_users_table_draws_column_separators():
    css = Path("app/static/css/admin.css").read_text(encoding="utf-8")
    block = css.split("#users-table th,\n#users-table td {", 1)[1].split("}", 1)[0]
    assert "border-right: 1px dashed" in block


@pytest.mark.unit
def test_storage_limit_is_an_editable_field():
    js = Path("app/static/js/admin.js").read_text(encoding="utf-8")
    assert "storage-limit-input" in js
    assert "saveStorageLimit" in js
    assert "storage_limit_mb" in js


@pytest.mark.unit
def test_users_table_fields_share_one_dark_theme_style():
    """Name, class and storage fields must look identical in the dark theme."""
    css = Path("app/static/css/admin.css").read_text(encoding="utf-8")
    block = css.split('body.dark-theme #users-table td input[type="text"],', 1)[1].split("}", 1)[0]
    assert 'input[type="number"]' in block
    assert "#users-table td select" in block
    assert "background: #2a2a2a" in block
