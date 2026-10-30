"""The task capability views must not change when they start being derived.

This file is a parity guard: it was written against the literal tables and must
keep passing after ``app/tasks/tools.py`` starts deriving them from
``app.tool_registry``.
"""

from __future__ import annotations

import pytest

from app.tasks.tools import CAPABILITIES, TOOL_BADGES, get_capabilities

EXPECTED_IDS = [
    "llm",
    "search",
    "rag",
    "history",
    "vision",
    "camera",
    "crawl",
    "calculate",
    "rlm",
    "continuity",
    "image_gen",
    "image_edit",
    "video_gen",
]

EXPECTED_BADGES = {
    "search": ("🔎", "Web search"),
    "rag": ("📄", "Document search"),
    "history": ("🕘", "Conversation history"),
    "vision": ("🖼️", "Image analysis"),
    "calculate": ("🧮", "Calculator"),
    "camera": ("📷", "Camera"),
    "crawl": ("🕸️", "Site study"),
    "rlm": ("🔬", "Deep analysis"),
    "continuity": ("📐", "Visual continuity"),
    "image_gen": ("🖼️", "Image generation"),
    "image_edit": ("🎨", "Image editing"),
    "video_gen": ("🎬", "Video generation"),
}


@pytest.mark.unit
def test_capabilities_keep_their_order():
    assert [entry["id"] for entry in CAPABILITIES] == EXPECTED_IDS


@pytest.mark.unit
def test_capabilities_keep_their_keys():
    for entry in CAPABILITIES:
        assert set(entry) == {"id", "label", "description", "checkpoint_hint"}


@pytest.mark.unit
def test_capabilities_keep_their_text():
    assert CAPABILITIES[0]["label"] == "Reasoning model"
    assert CAPABILITIES[0]["description"].startswith("Think, write, analyze")
    assert CAPABILITIES[8]["id"] == "rlm"
    assert CAPABILITIES[10]["description"] == "Generate an image from a text prompt."


@pytest.mark.unit
def test_badges_are_unchanged():
    assert TOOL_BADGES == EXPECTED_BADGES


@pytest.mark.unit
def test_get_capabilities_still_returns_copies():
    first = get_capabilities()
    first[0]["label"] = "mutated"
    assert get_capabilities()[0]["label"] == "Reasoning model"
