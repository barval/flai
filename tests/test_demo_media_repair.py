"""Guards for task-media rendering and the demo artifact-repair path.

The demo is generated: a task artifact must reach the task view, injection must
keep the task's plan and steps, and a rejected (best-effort) artifact must never
be published.
"""

import json
from pathlib import Path

import pytest

DEMO = Path("demo")
TASKS_JS = Path("app/static/js/chat-tasks.js")


def test_task_view_renders_a_toplevel_artifact():
    """The task view builds its media list from extra.media only; an artifact
    message that carries a top-level file_path (the injected-demo shape) would
    never render without a fallback."""
    source = TASKS_JS.read_text("utf-8")
    assert "m.file_path && m.file_type" in source


def _task_snapshot():
    return {
        "assets": {},
        "lang": "ru",
        "routes": [
            {
                "method": "GET",
                "path": "/api/tasks/abc/messages",
                "status": 200,
                "body": {
                    "task": {"id": "abc", "title": "prompt", "total_steps": 2},
                    "messages": [
                        {"role": "user", "kind": "request", "content": "prompt"},
                        {"role": "assistant", "kind": "plan", "content": "Plan of 2 steps:\n1. one\n2. two"},
                        {"role": "assistant", "kind": "subtask", "content": "Step 1 of 2: ..."},
                        {
                            "role": "assistant",
                            "kind": "subtask",
                            "content": "Step 2 of 2: ...",
                            "extra": {"media": {"file_path": "a/b.png", "file_type": "image/png"}},
                        },
                    ],
                },
            }
        ],
    }


def test_inject_media_keeps_task_steps_and_adds_renderable_extra(tmp_path, monkeypatch):
    from demo import edit

    monkeypatch.setattr(edit, "save_snapshot", lambda lang, snapshot: None)
    monkeypatch.setattr(edit, "_store_media", lambda lang, source: ("injected-test.mp4", "media/ru/injected-test.mp4"))

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 32)

    out = edit.inject_media(
        "ru",
        "prompt",
        video,
        snapshot=_task_snapshot(),
        model_name="wan2.2-ti2v-5b-turbo-Q4_K_M.gguf",
    )
    messages = out["routes"][0]["body"]["messages"]

    assert len(messages) == 5, "injection must keep the plan and every step of a task"
    injected = messages[-1]
    assert injected["file_path"] == "injected-test.mp4"
    assert injected["extra"]["media"]["file_path"] == "injected-test.mp4"
    assert injected["extra"]["media_all"][0]["file_type"] == "video/mp4"
    assert injected["model_name"] == "wan2.2-ti2v-5b-turbo-Q4_K_M.gguf"


def test_inject_media_requires_a_backend_for_video(tmp_path, monkeypatch):
    from demo import edit

    monkeypatch.setattr(edit, "save_snapshot", lambda lang, snapshot: None)
    monkeypatch.setattr(edit, "_store_media", lambda lang, source: ("injected-test.mp4", "media/ru/injected-test.mp4"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 32)

    with pytest.raises(ValueError):
        edit.inject_media("ru", "prompt", video, snapshot=_task_snapshot())


def test_rejected_media_is_a_capture_defect():
    from demo.capture import media_rejection_defects

    payload = {
        "messages": [
            {
                "role": "assistant",
                "kind": "subtask",
                "extra": {
                    "media": {"file_path": "x.png"},
                    "best_effort": True,
                    "attempts_log": [{"ok": False}, {"ok": False}, {"ok": False}],
                },
            },
            {
                "role": "assistant",
                "kind": "subtask",
                "extra": {"media": {"file_path": "y.png"}, "attempts_log": [{"ok": True}]},
            },
            {"role": "assistant", "kind": "final", "extra": {"final_items": [{"file_path": "z.png"}]}},
        ]
    }
    assert media_rejection_defects(payload) == 1


def test_astronaut_scenario_pins_a_photorealistic_sky():
    for lang, words in (("ru", ("реалистичн", "звёздн")), ("en", ("realistic", "starry"))):
        data = json.loads((DEMO / f"scenarios/{lang}.json").read_text("utf-8"))
        mars = next(s for s in data["scenarios"] if s.get("title") in ("Марс", "Mars"))
        prompt = mars["prompt"].lower()
        for word in words:
            assert word in prompt, f"{lang}: the astronaut scenario must pin {word!r}"
