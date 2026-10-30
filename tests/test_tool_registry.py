"""One manifest per capability, with every other view derived from it."""

from __future__ import annotations

import pytest

from app.tool_registry import (
    TOOL_SPECS,
    badges,
    capabilities,
    media_tools,
    spec_for,
    subtask_tools,
    subtask_types,
)


@pytest.mark.unit
def test_every_queue_capability_carries_a_task_type():
    # Without one the executor enqueues nothing and the step hangs.
    for spec in TOOL_SPECS:
        if spec.exec == "queue":
            assert spec.task_type, spec.id


@pytest.mark.unit
def test_every_direct_capability_carries_no_task_type():
    for spec in TOOL_SPECS:
        if spec.exec == "direct":
            assert spec.task_type is None, spec.id


@pytest.mark.unit
def test_exec_is_a_closed_set():
    assert {spec.exec for spec in TOOL_SPECS} <= {"direct", "queue"}


@pytest.mark.unit
def test_ids_are_unique():
    ids = [spec.id for spec in TOOL_SPECS]
    assert len(ids) == len(set(ids))


@pytest.mark.unit
def test_the_media_set_is_exactly_the_three_generators():
    # A text step with a candidate list reports "nothing was produced" as a
    # failed image.
    assert media_tools() == {"image_gen", "image_edit", "video_gen"}


@pytest.mark.unit
def test_media_is_a_subset_of_the_queue_capabilities():
    assert media_tools() <= subtask_tools()


@pytest.mark.unit
def test_the_prose_views_cover_the_same_ids_as_the_specs():
    assert {entry["id"] for entry in capabilities()} == {spec.id for spec in TOOL_SPECS}


@pytest.mark.unit
def test_every_badge_belongs_to_a_declared_capability():
    assert set(badges()) <= {spec.id for spec in TOOL_SPECS}


@pytest.mark.unit
def test_a_badge_is_an_emoji_and_a_msgid_pair():
    for tool_id, badge in badges().items():
        assert isinstance(badge, tuple) and len(badge) == 2, tool_id
        assert badge[0] and badge[1], tool_id


@pytest.mark.unit
def test_llm_runs_in_process_and_has_no_badge():
    spec = spec_for("llm")
    assert spec is not None
    assert spec.exec == "direct"
    assert "llm" not in badges()


@pytest.mark.unit
def test_an_unknown_id_has_no_spec():
    assert spec_for("no_such_capability") is None


@pytest.mark.unit
def test_the_routing_views_agree_with_the_specs():
    assert subtask_tools() == {spec.id for spec in TOOL_SPECS if spec.exec == "queue"}
    assert set(subtask_types()) == subtask_tools()
