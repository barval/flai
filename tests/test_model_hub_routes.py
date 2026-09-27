"""Route-level tests for the Model Hub API (service functions mocked)."""

import json
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


def _login_admin(client, test_app):
    with test_app.app_context():
        from app.userdb import create_user, get_user_by_login

        if not get_user_by_login("hubadmin"):
            create_user("hubadmin", "pass123", "Hub Admin", is_admin=True)
    client.post("/login", data={"login": "hubadmin", "password": "pass123"})


def test_hub_search_requires_admin(client):
    resp = client.get("/admin/api/hub/search?q=sql")
    assert resp.status_code == 403


def test_hub_search_ok(client, test_app):
    _login_admin(client, test_app)
    with patch(
        "app.routes.model_hub.model_hub.search_hf",
        return_value=[
            {
                "repo": "org/A",
                "downloads": 1,
                "likes": 0,
                "gated": False,
                "license": "apache-2.0",
                "files": [{"path": "a.gguf", "size_mb": 1.0, "sha256": ""}],
            },
        ],
    ):
        resp = client.get("/admin/api/hub/search?q=sql")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["items"][0]["repo"] == "org/A"


def test_hub_fit_blocked_unknown_arch(client, test_app):
    _login_admin(client, test_app)
    with patch(
        "app.routes.model_hub.model_hub.estimate_fit",
        side_effect=__import__("app.model_hub", fromlist=["DownloadBlocked"]).DownloadBlocked("unknown_arch"),
    ):
        resp = client.get("/admin/api/hub/fit?repo=org/A&file=a.gguf&module=multimodal")
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "unknown_arch"
    assert resp.get_json()["error"].startswith("⚠️ ")


def test_hub_download_ok_and_progress(client, test_app):
    _login_admin(client, test_app)
    with (
        patch("app.routes.model_hub.model_hub.start_download", return_value="abc123"),
        patch(
            "app.routes.model_hub.model_hub.get_job",
            return_value={
                "job_id": "abc123",
                "repo": "org/A",
                "path": "a.gguf",
                "filename": "a.gguf",
                "total_mb": 1200,
                "received_mb": 300,
                "speed_mb_s": 40,
                "state": "downloading",
                "error": "",
                "models_dir": "/models",
            },
        ),
    ):
        resp = client.post(
            "/admin/api/hub/download",
            data=json.dumps({"repo": "org/A", "file": "a.gguf"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["job_id"] == "abc123"
        resp2 = client.get("/admin/api/hub/progress/abc123")
        assert resp2.status_code == 200
        assert resp2.get_json()["job"]["state"] == "downloading"


def test_hub_download_blocked_gated(client, test_app):
    _login_admin(client, test_app)
    mb_dl = __import__("app.model_hub", fromlist=["DownloadBlocked"])
    with patch("app.routes.model_hub.model_hub.start_download", side_effect=mb_dl.DownloadBlocked("gated")):
        resp = client.post(
            "/admin/api/hub/download",
            data=json.dumps({"repo": "org/A", "file": "a.gguf"}),
            content_type="application/json",
        )
    assert resp.status_code == 409
    assert resp.get_json()["reason"] == "gated"
    assert resp.get_json()["error"].startswith("⚠️ ")


def test_hub_cancel(client, test_app):
    _login_admin(client, test_app)
    with patch("app.routes.model_hub.model_hub.cancel_job", return_value=True):
        resp = client.post("/admin/api/hub/cancel/abc123")
    assert resp.status_code == 200
    assert resp.get_json()["cancelled"] is True
