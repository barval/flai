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
    assert data["fits_computed"] is False


def test_hub_search_passes_context_and_marks_fits(client, test_app):
    _login_admin(client, test_app)
    captured = {}

    def _fake_search_hf(q, limit=20, context_length=None):
        captured["q"] = q
        captured["context_length"] = context_length
        return [{"repo": "org/A", "files": []}]

    with patch("app.routes.model_hub.model_hub.search_hf", side_effect=_fake_search_hf):
        resp = client.get("/admin/api/hub/search?q=sql&context=32768")
    assert resp.status_code == 200
    data = resp.get_json()
    assert captured["q"] == "sql"
    assert captured["context_length"] == 32768
    assert data["fits_computed"] is True


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


def test_hub_fit_uses_context_param(client, test_app):
    """The slider context overrides the module config default on /fit."""
    _login_admin(client, test_app)
    captured = {}

    def fake_estimate(repo, file_path, module=None, context_length=8192):
        captured["context"] = context_length
        captured["module"] = module
        return {
            "tier": "good",
            "platform": "gpu",
            "free_ram_mb": 12288,
            "message": "✓ Fits in VRAM",
        }

    with patch("app.routes.model_hub.model_hub.estimate_fit", side_effect=fake_estimate):
        resp = client.get("/admin/api/hub/fit?repo=org/A&file=a.gguf&module=reasoning&context=32768")
    assert resp.status_code == 200
    assert resp.get_json()["fit"]["platform"] == "gpu"
    assert captured["context"] == 32768
    assert captured["module"] == "reasoning"


def test_hub_fit_all_ok(client, test_app):
    """/fit-all returns fits for every model file of a repo in one call."""
    _login_admin(client, test_app)
    fake_fits = {
        "a.gguf": {"tier": "good", "platform": "gpu"},
        "b.gguf": {"error": "unknown_arch"},
    }
    with patch("app.routes.model_hub.model_hub.estimate_fits", return_value=fake_fits):
        resp = client.get("/admin/api/hub/fit-all?repo=org/A&module=multimodal&context=32768")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["fits"] == fake_fits
    assert body["context_length"] == 32768


def test_hub_fit_all_requires_repo(client, test_app):
    _login_admin(client, test_app)
    resp = client.get("/admin/api/hub/fit-all?repo=NOSLASH")
    assert resp.status_code == 400


def test_hub_download_ok_and_progress(client, test_app):
    _login_admin(client, test_app)
    with (
        patch("app.routes.model_hub.model_hub.start_download", return_value="abc123") as start_download,
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
        assert start_download.call_args.kwargs["companion_paths"] == []
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


def test_hub_download_passes_selected_companion_paths(client, test_app):
    _login_admin(client, test_app)
    with patch("app.routes.model_hub.model_hub.start_download", return_value="abc123") as start_download:
        resp = client.post(
            "/admin/api/hub/download",
            data=json.dumps({"repo": "org/A", "file": "main.gguf", "companions": ["draft-Q4_0.gguf"]}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    assert start_download.call_args.kwargs["companion_paths"] == ["draft-Q4_0.gguf"]


def test_hub_cancel(client, test_app):
    _login_admin(client, test_app)
    with patch("app.routes.model_hub.model_hub.cancel_job", return_value=True):
        resp = client.post("/admin/api/hub/cancel/abc123")
    assert resp.status_code == 200
    assert resp.get_json()["cancelled"] is True


def test_hub_search_reports_installed(client, test_app):
    """Search response lists already-downloaded basenames so the client can
    show the 'installed' badge and an in-place Delete button."""
    _login_admin(client, test_app)
    with (
        patch(
            "app.routes.model_hub.model_hub.search_hf",
            return_value=[{"repo": "org/A", "files": []}],
        ),
        patch("app.routes.model_hub.model_hub.installed_basenames", return_value=["a.gguf", "mtp-a.gguf"]),
    ):
        resp = client.get("/admin/api/hub/search?q=sql")
    assert resp.status_code == 200
    assert resp.get_json()["installed"] == ["a.gguf", "mtp-a.gguf"]


def test_hub_installed_lists_disk_files(client, test_app):
    _login_admin(client, test_app)
    with patch(
        "app.routes.model_hub.model_hub.list_installed",
        return_value=[{"name": "a.gguf", "size_mb": 1.0, "from_hub": True}],
    ):
        resp = client.get("/admin/api/hub/installed")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["files"] == [{"name": "a.gguf", "size_mb": 1.0, "from_hub": True}]


def test_hub_delete_ok(client, test_app):
    _login_admin(client, test_app)
    with patch(
        "app.routes.model_hub.model_hub.delete_installed",
        return_value={"removed": ["a.gguf", "mtp-a.gguf"], "from_hub": True},
    ):
        resp = client.post(
            "/admin/api/hub/delete",
            data=json.dumps({"filename": "a.gguf"}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["removed"] == ["a.gguf", "mtp-a.gguf"]


def test_hub_delete_in_use(client, test_app):
    """A model selected as the active reasoning/multimodal/embedding model
    must not be deletable."""
    _login_admin(client, test_app)

    def fake_config(module):
        return {"model_name": "a"} if module == "reasoning" else {}

    with (
        patch("app.routes.model_hub.model_hub.delete_installed", return_value={"removed": [], "from_hub": False}),
        patch("app.routes.model_hub.get_model_config", side_effect=fake_config),
    ):
        resp = client.post(
            "/admin/api/hub/delete",
            data=json.dumps({"filename": "a.gguf"}),
            content_type="application/json",
        )
    assert resp.status_code == 409
    assert resp.get_json()["reason"] == "in_use"


def test_hub_delete_bad_path(client, test_app):
    _login_admin(client, test_app)
    mb_dl = __import__("app.model_hub", fromlist=["DownloadBlocked"])
    with patch(
        "app.routes.model_hub.model_hub.delete_installed",
        side_effect=mb_dl.DownloadBlocked("bad_path"),
    ):
        resp = client.post(
            "/admin/api/hub/delete",
            data=json.dumps({"filename": "../evil.gguf"}),
            content_type="application/json",
        )
    assert resp.status_code == 400
    assert resp.get_json()["reason"] == "bad_path"


def test_hub_reachability(client, test_app):
    _login_admin(client, test_app)
    with patch("app.routes.model_hub.model_hub.check_reachability", return_value={"reachable": False}):
        resp = client.get("/admin/api/hub/reachability")
    assert resp.status_code == 200
    assert resp.get_json()["reachable"] is False
