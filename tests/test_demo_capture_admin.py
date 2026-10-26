"""Tests for the admin capture, the manifest and the cleanup plan."""

from demo.capture import capture_admin, cleanup_plan, load_manifest, write_manifest

MANIFEST = {
    "accounts": ["demo_ru_chat", "demo_ru_admin"],
    "media": ["media/ru/a.png"],
    "tasks": ["task-1"],
}


class FakeSession:
    def __init__(self):
        self.paths = []

    def get_json(self, path):
        self.paths.append(path)
        return {"path": path}


def test_capture_admin_records_every_endpoint():
    session = FakeSession()
    routes = capture_admin(session)["routes"]
    assert [route["path"] for route in routes] == session.paths
    assert routes[0]["path"] == "/admin/api/hardware"
    assert len(routes) == 15
    assert any(route["path"] == "/admin/api/monitoring?range=30d" for route in routes)


def test_cleanup_plan_names_every_residue():
    plan = cleanup_plan(MANIFEST)
    assert plan["logins"] == ["demo_ru_chat", "demo_ru_admin"]
    assert plan["tables"] == ["tasks", "task_steps"]
    assert plan["folders"] == ["demo_ru_chat", "demo_ru_admin"]
    assert plan["media"] == ["media/ru/a.png"]


def test_manifest_round_trip(tmp_path):
    path = tmp_path / "ru.manifest.json"
    write_manifest(path, MANIFEST)
    assert load_manifest(path) == MANIFEST
