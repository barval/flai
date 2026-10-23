# tests/test_settings.py
"""Admin-editable settings: registry coverage, precedence, validation, audit."""

import re
from pathlib import Path

import pytest

from app.settings_registry import BOOTSTRAP_KEYS, GROUPS, movable_keys

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
    client.post("/login", data={"login": "admin", "password": "adminpass"})
    return client


def _clear(keys=("RAG_TOP_K", "TAVILY_TIMEOUT", "CRAWL_API_TOKEN", "TAVILY_ENABLED")):
    from app.database import get_db

    with get_db() as conn:
        conn.cursor().execute("DELETE FROM settings WHERE key = ANY(%s)", (list(keys),))


@pytest.mark.unit
def test_every_env_key_is_classified():
    """A new environment variable must be either editable or bootstrap-only."""
    source = Path("app/config.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'os\.getenv\("([A-Z0-9_]+)"', source))
    covered = set(movable_keys()) | BOOTSTRAP_KEYS
    missing = sorted(keys - covered)
    assert not missing, f"unclassified env keys: {missing}"


@pytest.mark.unit
def test_no_key_is_listed_twice():
    keys = movable_keys()
    assert len(keys) == len(set(keys))
    assert not (set(keys) & BOOTSTRAP_KEYS)
    assert all(group_keys for _label, group_keys in GROUPS.values())


def test_saved_value_overrides_the_env_default(test_app):
    from app.settings_store import save_values

    with test_app.app_context():
        _clear()
        default = test_app.config["RAG_TOP_K"]

        saved, errors = save_values(test_app, {"RAG_TOP_K": default + 1}, "admin")

        assert errors == {}
        assert saved == {"RAG_TOP_K": default + 1}
        assert test_app.config["RAG_TOP_K"] == default + 1
        _clear()


def test_invalid_value_is_rejected_and_not_saved(admin_client, test_app):
    response = admin_client.post("/admin/api/settings", json={"values": {"RAG_TOP_K": "abc"}})

    assert response.status_code == 400
    assert response.get_json()["error"].startswith("⚠️ ")
    assert "RAG_TOP_K" in response.get_json()["fields"]


def test_reset_restores_the_env_default(admin_client, test_app):
    with test_app.app_context():
        _clear()
        default = test_app.config["RAG_TOP_K"]

    assert admin_client.post("/admin/api/settings", json={"values": {"RAG_TOP_K": default + 3}}).status_code == 200
    assert admin_client.post("/admin/api/settings/reset", json={"keys": ["RAG_TOP_K"]}).status_code == 200

    with test_app.app_context():
        assert test_app.config["RAG_TOP_K"] == default
        from app.settings_store import load_overrides

        assert "RAG_TOP_K" not in load_overrides()


def test_secret_values_are_never_returned(admin_client, test_app):
    with test_app.app_context():
        _clear()
        test_app.config["CRAWL_API_TOKEN"] = "super-secret-token"

    body = admin_client.get("/admin/api/settings").get_json()
    field = next(
        field for group in body["groups"] for field in group["fields"] if field["key"] == "CRAWL_API_TOKEN"
    )

    assert field["secret"] is True
    assert field["value"] is None
    assert field["has_value"] is True
    assert "super-secret-token" not in admin_client.get("/admin/api/settings").get_data(as_text=True)


def test_changes_are_audited(admin_client, test_app):
    with test_app.app_context():
        _clear()

    admin_client.post("/admin/api/settings", json={"values": {"TAVILY_TIMEOUT": 25}})
    rows = admin_client.get("/admin/api/settings/audit?limit=5").get_json()

    assert rows[0]["key"] == "TAVILY_TIMEOUT"
    assert rows[0]["new_value"] == "25"
    assert rows[0]["updated_by"] == "admin"
    with test_app.app_context():
        _clear()


def test_settings_tab_requires_an_admin(client):
    assert client.get("/admin/api/settings").status_code in (302, 401, 403)


def test_full_backup_includes_the_settings_tables():
    from app.routes.backups import FULL_TABLES

    assert "settings" in FULL_TABLES
    assert "settings_audit" in FULL_TABLES
