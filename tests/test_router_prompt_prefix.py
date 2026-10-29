"""The router prompt must keep its static head cacheable.

The camera room list and every per-request section used to sit in the middle of
the template, so llama.cpp had to re-prefill everything after them on every
message. Moving the volatile sections under ``INPUT DATA`` leaves the head
byte-identical across requests, which is what makes the prefix cache hit.
"""

from __future__ import annotations

import pytest

from modules.base import BaseModule


def _builder():
    module = BaseModule.__new__(BaseModule)  # the prompt builder needs no app context
    counter = {"n": 0}

    def fake_camera(lang: str = "ru") -> str:
        counter["n"] += 1
        return f"CAMERA-BLOCK-{counter['n']}"

    module._build_camera_prompt_section = fake_camera  # type: ignore[method-assign]
    return module


@pytest.mark.unit
@pytest.mark.parametrize(("lang", "marker"), [("en", "# INPUT DATA"), ("ru", "# ВХОДНЫЕ ДАННЫЕ")])
def test_static_head_is_identical_across_volatile_inputs(lang: str, marker: str) -> None:
    module = _builder()
    first = module._build_router_prompt(
        "show the kitchen", "2026-10-30 10:00", lang=lang, recent_context="User: hi"
    )
    second = module._build_router_prompt(
        "show the garage", "2026-10-30 23:59", lang=lang, recent_context="User: something else entirely"
    )

    head = first.index(marker)
    assert first[:head] == second[:head]
    assert "CAMERA-BLOCK" not in first[:head]


@pytest.mark.unit
@pytest.mark.parametrize(("lang", "marker"), [("en", "# INPUT DATA"), ("ru", "# ВХОДНЫЕ ДАННЫЕ")])
def test_the_static_head_has_no_unfilled_placeholders(lang: str, marker: str) -> None:
    module = _builder()
    prompt = module._build_router_prompt("q", "2026-10-30", lang=lang, recent_context="")
    head = prompt[: prompt.index(marker)]
    assert "{" not in head
