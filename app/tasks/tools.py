# app/tasks/tools.py
"""Capabilities registry for the Tasks subsystem (future skills seam).

TOOL_RUNNERS is the single source of truth for direct tool runners;
later tasks register additional runners into this dict. CAPABILITIES
lists the tool ids the plan prompt advertises to the planner model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _run_calculate(expression: str) -> Any:
    from app.tools import safe_eval

    return safe_eval(expression)


TOOL_RUNNERS: dict[str, Callable] = {"calculate": _run_calculate}

CAPABILITIES: list[dict[str, str]] = [
    {
        "id": "llm",
        "label": "Reasoning model",
        "description": "Think, write, analyze, derive, write code without fresh internet data.",
        "checkpoint_hint": "State the concrete artifact or answer the reasoning must produce.",
    },
    {
        "id": "search",
        "label": "Web search",
        "description": "Search the internet for fresh data (rates, news, current facts).",
        "checkpoint_hint": "Name the fact or data the search must confirm.",
    },
    {
        "id": "calculate",
        "label": "Calculator",
        "description": "Evaluate a math expression safely (arithmetic and math functions).",
        "checkpoint_hint": "State the expression and its expected exact result.",
    },
    {
        "id": "rlm",
        "label": "Deep analysis",
        "description": "Multi-step deep analysis over selected documents, images and web pages.",
        "checkpoint_hint": "Name the documents and the question the analysis must answer.",
    },
    {
        "id": "image_gen",
        "label": "Image generation",
        "description": "Generate an image from a text prompt.",
        "checkpoint_hint": "Describe the subject the generated image must show.",
    },
    {
        "id": "image_edit",
        "label": "Image editing",
        "description": "Edit an existing image according to an instruction.",
        "checkpoint_hint": "Name the source image and the change that must be visible.",
    },
    {
        "id": "video_gen",
        "label": "Video generation",
        "description": "Generate a short video from a text prompt.",
        "checkpoint_hint": "Describe the scene and motion the video must show.",
    },
]


def get_capabilities() -> list[dict[str, str]]:
    return CAPABILITIES
