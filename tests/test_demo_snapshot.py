"""Tests for the snapshot route table and the inline asset map."""

from demo.snapshot import compile_assets, compile_routes, find_route, route_body

SNAPSHOT = {
    "routes": [
        {"method": "GET", "path": "/api/sessions", "status": 200, "body": [{"id": "s1"}]},
        {"method": "GET", "path": "/api/sessions/s1/messages", "status": 200, "body": {"messages": []}},
    ],
    "assets": {"/api/files/a.png": "media/ru/a.png", "/api/files/v.mp4": "media/ru/v.mp4"},
}


def test_find_route_ignores_query():
    assert find_route(SNAPSHOT, "GET", "/api/sessions")["body"] == [{"id": "s1"}]


def test_route_body_by_path():
    assert route_body(SNAPSHOT, "GET", "/api/sessions/s1/messages") == {"messages": []}


def test_unknown_route_returns_none():
    assert find_route(SNAPSHOT, "POST", "/api/sessions") is None


def test_compile_routes_shape():
    assert compile_routes(SNAPSHOT)[0] == {"m": "GET", "p": "/api/sessions", "s": 200, "b": [{"id": "s1"}]}


def test_compile_assets_prefixes_mime(tmp_path):
    (tmp_path / "media/ru").mkdir(parents=True)
    (tmp_path / "media/ru/a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "media/ru/v.mp4").write_bytes(b"0000")
    assets = compile_assets(SNAPSHOT, tmp_path)
    assert assets["/api/files/a.png"].startswith("data:image/png;base64,")
    assert assets["/api/files/v.mp4"].startswith("data:video/mp4;base64,")
