# tests/test_cam_service_v13.py
"""Tests for the room-snapshot-api v1.3 integration (key, lang, ETag, fan-out, MCP)."""

from unittest.mock import MagicMock, patch

import pytest


def _cam(**config):
    from modules.cam import CamModule

    app = MagicMock()
    app.config = {
        "CAMERA_API_URL": "http://cam:5000",
        "CAMERA_API_KEY": "secret-key",
        "CAMERA_MCP_URL": "http://cam:5006/mcp",
        "CAMERA_API_TIMEOUT": 5,
        "CAMERA_CHECK_INTERVAL": 30,
        "CAMERA_SNAPSHOT_WIDTH": 384,
        "CAMERA_PREVIEW_WORKERS": 3,
    }
    app.config.update(config)
    module = CamModule()
    module.app = app
    module.camera_api_url = app.config["CAMERA_API_URL"]
    module.timeout = app.config["CAMERA_API_TIMEOUT"]
    module.check_interval = app.config["CAMERA_CHECK_INTERVAL"]
    module.api_key = app.config["CAMERA_API_KEY"]
    module.mcp_url = app.config["CAMERA_MCP_URL"]
    module.snapshot_width = app.config["CAMERA_SNAPSHOT_WIDTH"]
    module.preview_workers = app.config["CAMERA_PREVIEW_WORKERS"]
    return module


@pytest.mark.unit
class TestHeaders:
    def test_key_and_language_are_sent(self):
        headers = _cam().api_headers(lang="ru", accept="image/jpeg")
        assert headers["X-API-Key"] == "secret-key"
        assert headers["Accept-Language"] == "ru"
        assert headers["Accept"] == "image/jpeg"

    def test_no_key_means_no_header(self):
        headers = _cam(CAMERA_API_KEY="").api_headers()
        assert "X-API-Key" not in headers


@pytest.mark.unit
class TestSnapshot:
    def test_etag_revalidation(self):
        cam = _cam()
        response = MagicMock(status_code=304, content=b"", headers={})
        with patch("modules.cam.requests.get", return_value=response) as get:
            result = cam.fetch_snapshot("kuh", lang="ru", width=384, etag='"abc"')
        assert result["status"] == 304
        assert get.call_args.kwargs["headers"]["If-None-Match"] == '"abc"'
        assert get.call_args.kwargs["params"]["lang"] == "ru"
        assert get.call_args.kwargs["params"]["width"] == "384"

    def test_snapshot_success_carries_the_etag(self):
        cam = _cam()
        response = MagicMock(status_code=200, content=b"jpeg", headers={"ETag": '"v1"', "content-type": "image/jpeg"})
        with patch("modules.cam.requests.get", return_value=response):
            result = cam.fetch_snapshot("kuh")
        assert result["status"] == 200
        assert result["content"] == b"jpeg"
        assert result["etag"] == '"v1"'

    def test_transport_error_is_503(self):
        import requests

        cam = _cam()
        with patch("modules.cam.requests.get", side_effect=requests.exceptions.ConnectionError("down")):
            assert cam.fetch_snapshot("kuh")["status"] == 503

    def test_fan_out_covers_every_camera(self):
        cam = _cam()
        seen: list[str] = []

        def fake(code, lang="ru", width=None, etag=None):
            seen.append(code)
            return {"status": 200, "content": b"x", "etag": "", "content_type": "image/jpeg"}

        cam.fetch_snapshot = fake  # type: ignore[method-assign]
        result = cam.snapshot_many(["kuh", "gos", "det"], lang="ru")
        assert sorted(result) == ["det", "gos", "kuh"]
        assert sorted(seen) == ["det", "gos", "kuh"]

    def test_fan_out_survives_one_broken_camera(self):
        cam = _cam()

        def fake(code, lang="ru", width=None, etag=None):
            if code == "bad":
                raise RuntimeError("boom")
            return {"status": 200, "content": b"x", "etag": "", "content_type": "image/jpeg"}

        cam.fetch_snapshot = fake  # type: ignore[method-assign]
        result = cam.snapshot_many(["kuh", "bad"])
        assert result["kuh"]["status"] == 200
        assert result["bad"]["status"] == 503


@pytest.mark.unit
class TestHealth:
    def _health(self, payload):
        cam = _cam()
        response = MagicMock(status_code=200)
        response.json.return_value = payload
        with patch("modules.cam.requests.get", return_value=response):
            return cam.health_details()

    def test_v12_shape(self):
        detail = self._health({"status": "ok", "cameras": ["kuh", "gos"], "ffmpeg_available": True})
        assert detail["status"] == "ok"
        assert detail["cameras_total"] == 2
        assert detail["cameras_online"] == 2

    def test_v13_shape_counts_reachable_cameras(self):
        detail = self._health(
            {"status": "degraded", "cameras": ["kuh", "gos"], "cameras_status": {"kuh": True, "gos": False}}
        )
        assert detail["status"] == "degraded"
        assert detail["cameras_online"] == 1
        assert detail["cameras_total"] == 2

    def test_unreachable_service(self):
        import requests

        cam = _cam()
        with patch("modules.cam.requests.get", side_effect=requests.exceptions.ConnectionError("no")):
            assert cam.health_details()["status"] == "unreachable"


@pytest.mark.unit
class TestMCPClient:
    def test_unconfigured(self):
        from modules.cam_mcp import CamMCPClient

        assert CamMCPClient("").status() == {"configured": False, "available": False, "tools": []}

    def test_tools_are_listed(self):
        from modules.cam_mcp import CamMCPClient

        client = CamMCPClient("http://cam:5006/mcp", api_key="k")
        responses = [
            MagicMock(status_code=200, headers={}, json=lambda: {"result": {"protocolVersion": "2025-06-18"}}),
            MagicMock(status_code=200, headers={}, json=lambda: {}),
            MagicMock(
                status_code=200,
                headers={},
                json=lambda: {
                    "result": {
                        "tools": [{"name": "list_rooms"}, {"name": "get_snapshot"}, {"name": "room_info"}]
                    }
                },
            ),
        ]
        with patch("modules.cam_mcp.requests.post", side_effect=responses):
            status = client.status()
        assert status["available"] is True
        assert status["tools"] == ["list_rooms", "get_snapshot", "room_info"]

    def test_sse_response_is_parsed(self):
        from modules.cam_mcp import _parse_sse

        body = 'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}\n\n'
        assert _parse_sse(body)["result"] == {"tools": []}

    def test_transport_failure_marks_unavailable(self):
        from modules.cam_mcp import CamMCPClient

        client = CamMCPClient("http://cam:5006/mcp", api_key="k")
        with patch("modules.cam_mcp.requests.post", side_effect=RuntimeError("down")):
            status = client.status()
        assert status["configured"] is True
        assert status["available"] is False


@pytest.mark.unit
class TestRoomSync:
    def test_rooms_request_carries_key_and_language(self):
        """v1.3 answers 401 without the key and English labels without the language."""
        import app.cameradb as camerdb

        response = MagicMock(status_code=200)
        response.json.return_value = {"kuh": "Кухня"}
        conn = MagicMock()
        with (
            patch("app.cameradb.requests.get", return_value=response) as get,
            patch("app.cameradb.get_db") as db,
            patch("app.cameradb.generate_room_name_forms", return_value=["кухня"]),
        ):
            db.return_value.__enter__.return_value = conn
            codes = camerdb.populate_from_camera_api(
                "http://cam:5000", 5, headers={"X-API-Key": "k", "Accept-Language": "ru"}
            )

        assert codes == ["kuh"]
        assert get.call_args.kwargs["headers"] == {"X-API-Key": "k", "Accept-Language": "ru"}

    def test_no_headers_means_an_anonymous_request(self):
        import app.cameradb as camerdb

        response = MagicMock(status_code=200)
        response.json.return_value = {"gos": "Living room"}
        with (
            patch("app.cameradb.requests.get", return_value=response) as get,
            patch("app.cameradb.get_db"),
            patch("app.cameradb.generate_room_name_forms", return_value=["living room"]),
        ):
            camerdb.populate_from_camera_api("http://cam:5000")

        assert get.call_args.kwargs["headers"] == {}
