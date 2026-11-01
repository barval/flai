"""The kernel prompt: a tool catalog plus one JSON answer."""

from __future__ import annotations

import pytest

from app.kernel import KERNEL_INTENTS, KERNEL_RESPONSE_SCHEMA, build_kernel_catalog, build_kernel_prompt
from app.utils import estimate_tokens


@pytest.mark.unit
def test_catalog_names_every_tool_without_its_schema():
    from app.tools import TOOL_DEFINITIONS

    catalog = build_kernel_catalog(TOOL_DEFINITIONS)
    for item in TOOL_DEFINITIONS:
        assert f"- {item['function']['name']}:" in catalog
    assert "parameters" not in catalog


@pytest.mark.unit
def test_catalog_is_small():
    from app.tools import TOOL_DEFINITIONS

    assert estimate_tokens(build_kernel_catalog(TOOL_DEFINITIONS), "multimodal", "en") < 200


@pytest.mark.unit
def test_catalog_survives_a_tool_without_a_description():
    catalog = build_kernel_catalog([{"function": {"name": "mystery"}}])
    assert catalog.strip() == "- mystery:"


@pytest.mark.unit
def test_prompt_ends_with_the_user_message():
    prompt = build_kernel_prompt("покажи гостиную", "- camera_snapshot: room", "2026-10-30", "User: hi")
    assert prompt.rstrip().endswith('"покажи гостиную"')


@pytest.mark.unit
def test_prompt_puts_the_catalog_before_the_volatile_sections():
    prompt = build_kernel_prompt("q", "- camera_snapshot: room", "2026-10-30", "User: hi")
    assert prompt.index("camera_snapshot") < prompt.index("2026-10-30")
    assert prompt.index("camera_snapshot") < prompt.index("User: hi")


@pytest.mark.unit
def test_prompt_lists_every_intent():
    prompt = build_kernel_prompt("q", "- x: y", "2026-10-30", "")
    for intent in KERNEL_INTENTS:
        assert intent in prompt


@pytest.mark.unit
def test_schema_enumerates_the_intents():
    assert KERNEL_RESPONSE_SCHEMA["properties"]["intent"]["enum"] == list(KERNEL_INTENTS)


@pytest.mark.unit
def test_schema_requires_an_intent_and_caps_the_tools():
    assert "intent" in KERNEL_RESPONSE_SCHEMA["required"]
    assert KERNEL_RESPONSE_SCHEMA["properties"]["tools"]["maxItems"] == 6


@pytest.mark.unit
def test_prompt_carries_the_room_list_after_the_catalog():
    prompt = build_kernel_prompt("q", "- x: y", "2026-10-30", "", rooms="gos (living room), kab (study)")
    assert "gos (living room)" in prompt
    assert prompt.index("x: y") < prompt.index("gos (living room)")


@pytest.mark.unit
def test_prompt_without_rooms_still_renders():
    prompt = build_kernel_prompt("q", "- x: y", "2026-10-30", "")
    assert "Rooms known to this installation" in prompt
