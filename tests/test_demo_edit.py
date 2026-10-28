# tests/test_demo_edit.py
"""Surgical demo edits: partial re-runs and injected artifacts."""

import json

import pytest

from demo import edit as demo_edit


def _snapshot(prompt="Кафе"):
    return {
        "lang": "ru",
        "routes": [
            {
                "path": "/api/sessions",
                "body": [{"id": "s1", "message_count": 2}],
            },
            {
                "path": "/api/sessions/s1/messages?limit=100&offset=0",
                "body": {
                    "messages": [
                        {"role": "user", "content": json.dumps({"text": f"{prompt} — сделай клип"}, ensure_ascii=False)},
                        {"role": "assistant", "content": "старый ответ", "file_type": "text/plain"},
                    ]
                },
            },
            {"path": "/api/other", "body": {"keep": True}},
        ],
        "assets": {},
    }


@pytest.mark.unit
class TestInject:
    def test_injects_a_video_and_keeps_the_user_turn(self, tmp_path, monkeypatch):
        monkeypatch.setattr(demo_edit, "DEMO_DIR", tmp_path)
        (tmp_path / "media" / "ru").mkdir(parents=True)
        movie = tmp_path / "clip.mp4"
        movie.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        snapshot = _snapshot()

        result = demo_edit.inject_media("ru", "Кафе", movie, answer="готово", snapshot=snapshot)

        route = result["routes"][1]
        messages = route["body"]["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["file_type"] == "video/mp4"
        assert messages[1]["content"] == "готово"
        assert result["assets"][messages[1]["file_path"]].startswith("media/ru/injected-")

    def test_unknown_scenario_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(demo_edit, "DEMO_DIR", tmp_path)
        movie = tmp_path / "clip.mp4"
        movie.write_bytes(b"x")
        with pytest.raises(LookupError):
            demo_edit.inject_media("ru", "Несуществующий", movie, snapshot=_snapshot())

    def test_unsupported_format_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(demo_edit, "DEMO_DIR", tmp_path)
        bad = tmp_path / "clip.txt"
        bad.write_text("nope")
        with pytest.raises(ValueError):
            demo_edit.inject_media("ru", "Кафе", bad, snapshot=_snapshot())


@pytest.mark.unit
class TestPartialMerge:
    def test_rerun_replaces_only_its_own_routes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("demo.capture.DEMO_DIR", tmp_path)
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()
        old = _snapshot()
        (snapshot_dir / "ru.json").write_text(json.dumps(old), encoding="utf-8")

        fresh = {
            "routes": [
                {"path": "/api/sessions", "body": [{"id": "s2", "message_count": 2}]},
                {
                    "path": "/api/sessions/s2/messages?limit=100&offset=0",
                    "body": {"messages": [{"role": "user", "content": json.dumps({"text": "Кафе — сделай клип"}, ensure_ascii=False)}]},
                },
                {"path": "/api/admin/api/users", "body": []},
            ],
            "assets": {"new.mp4": "media/ru/new.mp4"},
        }

        from demo.capture import _merge_partial

        merged = _merge_partial(fresh, "ru", ["Кафе — сделай клип"])

        paths = [route["path"] for route in merged["routes"]]
        assert "/api/sessions/s1/messages?limit=100&offset=0" not in paths  # old copy dropped
        assert "/api/sessions/s2/messages?limit=100&offset=0" in paths
        assert "/api/other" in paths  # untouched scenario survives
        assert merged["assets"]["new.mp4"] == "media/ru/new.mp4"

    def test_empty_lists_do_not_blank_the_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setattr("demo.capture.DEMO_DIR", tmp_path)
        snapshot_dir = tmp_path / "snapshot"
        snapshot_dir.mkdir()
        (snapshot_dir / "ru.json").write_text(json.dumps(_snapshot()), encoding="utf-8")

        fresh = {
            "routes": [
                {"path": "/api/sessions", "body": []},
                {"path": "/api/tasks", "body": {"tasks": []}},
            ],
            "assets": {},
        }

        from demo.capture import _merge_partial

        merged = _merge_partial(fresh, "ru", [])
        sessions = [route for route in merged["routes"] if route["path"] == "/api/sessions"]
        assert sessions and sessions[0]["body"], "the captured session list must survive"
