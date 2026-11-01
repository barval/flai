"""The executor's routing tables must come from the manifest, not from literals.

Written before the literals were replaced: the assertions compare the tables with
the manifest, so they hold both before and after. A refactor that drops a
capability from one of the three tables fails here.
"""

from __future__ import annotations

import pytest

from app.tasks.executor import _MEDIA_TOOLS, _SUBTASK_TOOLS, _SUBTASK_TYPE
from app.tool_registry import TOOL_SPECS, media_tools, subtask_tools, subtask_types


@pytest.mark.unit
def test_the_routing_tables_match_the_manifest():
    assert subtask_tools() == _SUBTASK_TOOLS
    assert subtask_types() == _SUBTASK_TYPE
    assert media_tools() == _MEDIA_TOOLS


@pytest.mark.unit
def test_no_queue_capability_is_missing_from_the_table():
    queue_ids = {spec.id for spec in TOOL_SPECS if spec.exec == "queue"}
    assert queue_ids == set(_SUBTASK_TOOLS)
    assert queue_ids == set(_SUBTASK_TYPE)


@pytest.mark.unit
def test_no_direct_capability_leaked_into_the_queue_tables():
    direct_ids = {spec.id for spec in TOOL_SPECS if spec.exec == "direct"}
    assert not (direct_ids & set(_SUBTASK_TOOLS))
    assert not (direct_ids & set(_SUBTASK_TYPE))
    assert "llm" not in _SUBTASK_TOOLS


@pytest.mark.unit
def test_every_queue_task_type_names_a_real_queue_task():
    # A typo here enqueues nothing and the step waits forever.
    for tool_id, task_type in _SUBTASK_TYPE.items():
        assert isinstance(task_type, str) and task_type, tool_id


@pytest.mark.unit
def test_the_media_set_is_still_the_three_generators():
    assert {"image_gen", "image_edit", "video_gen"} == _MEDIA_TOOLS
