"""The camera surface of the tool loop: list the rooms, describe one."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from app import tools


def _ctx(cam_available: bool = True, multimodal: bool = True) -> dict:
    cam = Mock()
    cam.available = cam_available
    cam.get_available_rooms.return_value = {"kuh": "Кухня", "gos": "Гостиная"}
    cam.get_room_name.side_effect = lambda code, lang="ru": {"kuh": "Кухня", "gos": "Гостиная"}[code]
    cam.get_snapshot.return_value = {
        "success": True,
        "room_name": "Кухня",
        "image_data": "AAAA",
        "image_type": "image/jpeg",
        "file_name": "kuh.jpg",
        "file_size": 3,
    }
    mm = Mock()
    mm.available = multimodal
    mm.process_image_with_text.return_value = ("На кухне кошка у окна", None)
    app = Mock()
    app.modules = {"cam": cam, "multimodal": mm}
    return {"app": app, "user_id": "user", "lang": "ru"}


@pytest.mark.unit
def test_the_tools_list_the_rooms_with_codes_and_names():
    result = tools._exec_list_rooms(_ctx())
    assert result["success"] is True
    assert result["rooms"] == [{"code": "gos", "name": "Гостиная"}, {"code": "kuh", "name": "Кухня"}]


@pytest.mark.unit
def test_listing_rooms_without_a_camera_service_is_an_error():
    result = tools._exec_list_rooms(_ctx(cam_available=False))
    assert result["success"] is False
    assert result["error"]


@pytest.mark.unit
def test_a_snapshot_tool_returns_a_description_not_an_image():
    # The base64 image would burn the window and tell the model nothing.
    ctx = _ctx()
    result = tools._exec_camera_snapshot(ctx, "kuh")
    assert result == {"success": True, "room": "Кухня", "description": "На кухне кошка у окна"}
    described_image = ctx["app"].modules["multimodal"].process_image_with_text.call_args.args[0]
    assert described_image == "AAAA"


@pytest.mark.unit
def test_a_snapshot_failure_stays_a_failure():
    ctx = _ctx()
    ctx["app"].modules["cam"].get_snapshot.return_value = {"success": False, "error": "нет сигнала"}
    result = tools._exec_camera_snapshot(ctx, "kuh")
    assert result == {"success": False, "error": "нет сигнала"}


@pytest.mark.unit
def test_without_a_vision_model_the_snapshot_is_still_reported():
    ctx = _ctx(multimodal=False)
    result = tools._exec_camera_snapshot(ctx, "kuh")
    assert result["success"] is True
    assert result["room"] == "Кухня"
    assert "AAAA" not in str(result)


@pytest.mark.unit
def test_the_catalogue_documents_what_the_snapshot_tool_returns():
    definition = next(d for d in tools.TOOL_DEFINITIONS if d["function"]["name"] == "camera_snapshot")
    assert "DESCRIPTION" in definition["function"]["description"]


@pytest.mark.unit
def test_list_rooms_is_offered_only_with_a_camera_module(app):
    with app.app_context(), patch("app.skills.discover", return_value=[]), patch("app.tools._camera_available", return_value=False):
        names = [d["function"]["name"] for d in tools.get_tool_definitions("ru")]
    assert "list_rooms" not in names

    with app.app_context(), patch("app.skills.discover", return_value=[]), patch("app.tools._camera_available", return_value=True):
        names = [d["function"]["name"] for d in tools.get_tool_definitions("ru")]
    assert "list_rooms" in names
