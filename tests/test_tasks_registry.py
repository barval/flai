# tests/test_tasks_registry.py
"""Capabilities registry for the Tasks subsystem: core ids and calculate runner."""

from app.tasks.tools import TOOL_RUNNERS, get_capabilities


def test_registry_contains_core_tools():
    caps = {c["id"] for c in get_capabilities()}
    assert {"llm", "search", "calculate", "rlm", "image_gen", "image_edit", "video_gen"} == caps


def test_registry_entries_complete():
    for cap in get_capabilities():
        assert set(cap.keys()) == {"id", "label", "description", "checkpoint_hint"}
        assert all(isinstance(v, str) and v.strip() for v in cap.values())


def test_tool_runners_single_source():
    assert set(TOOL_RUNNERS.keys()) == {"calculate"}


def test_calculate_runner():
    run = TOOL_RUNNERS["calculate"]
    assert run("2+2*3") == 8


def test_get_capabilities_returns_defensive_copies():
    caps = get_capabilities()
    caps.append({"id": "ghost"})
    caps[0]["label"] = "x"
    fresh = get_capabilities()
    assert {c["id"] for c in fresh} == {"llm", "search", "calculate", "rlm", "image_gen", "image_edit", "video_gen"}
    assert fresh[0]["label"] != "x"
