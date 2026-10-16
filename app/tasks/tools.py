# app/tasks/tools.py
"""Capabilities registry for the Tasks subsystem (future skills seam).

TOOL_RUNNERS is the single source of truth for direct tool runners;
later tasks register additional runners into this dict. CAPABILITIES
lists the tool ids the plan prompt advertises to the planner model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def N_(message: str) -> str:  # noqa: N802 — gettext lazy marker (Babel default keyword)
    """Mark a literal for Babel extraction without translating it now.

    The badge names live in a module-level dict, so an ``_()`` call would be
    invisible to ``pybabel extract`` (it only sees literal ``_("…")`` calls) and
    the msgids would go obsolete on the next catalog update — they then vanish
    from the compiled ``.mo`` and the badge falls back to English. ``N_`` is a
    Babel default keyword, so wrapping the literals keeps them extracted while
    still returning the untranslated string here; the render site applies ``_()``.
    """
    return message


def _run_calculate(expression: str) -> Any:
    from app.tools import safe_eval

    return safe_eval(expression)


TOOL_RUNNERS: dict[str, Callable] = {"calculate": _run_calculate}

# Display badge for a step whose content a TOOL produced (not the model):
# emoji + the gettext msgid of its localized name. The API layer renders the
# msgid inside the request locale and prefixes the step's answer line with it.
TOOL_BADGES: dict[str, tuple[str, str]] = {
    "search": ("🔎", N_("Web search")),
    "rag": ("📄", N_("Document search")),
    "history": ("🕘", N_("Conversation history")),
    "vision": ("🖼️", N_("Image analysis")),
    "calculate": ("🧮", N_("Calculator")),
    "camera": ("📷", N_("Camera")),
    "crawl": ("🕸️", N_("Site study")),
    "rlm": ("🔬", N_("Deep analysis")),
    "image_gen": ("🖼️", N_("Image generation")),
    "image_edit": ("🎨", N_("Image editing")),
    "video_gen": ("🎬", N_("Video generation")),
}

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
        "id": "rag",
        "label": "Document search",
        "description": (
            "Search the user's own documents and return the relevant fragments. "
            "Pair with a following llm step that composes the answer from them."
        ),
        "checkpoint_hint": "Name the data the search must find in the documents.",
    },
    {
        "id": "history",
        "label": "Conversation history",
        "description": (
            "Search the user's past conversations for a topic and return matching "
            "fragments (what was discussed earlier). Pair with a following llm step "
            "that composes the answer from them."
        ),
        "checkpoint_hint": "Name the topic the past conversation must contain.",
    },
    {
        "id": "vision",
        "label": "Image analysis",
        "description": (
            "Extract data from ONE image attached to the task with the multimodal "
            "model: a detailed description of everything visible plus all text on the "
            "image. It does not answer the task question — pair it with a following "
            "llm step that composes the answer from the extraction. To analyze "
            "several images, plan ONE step per image with that image's file name in "
            "the goal: the step reads only the image its goal names. Use only when "
            "images are attached to the task."
        ),
        "checkpoint_hint": "Name this image's file in the goal and require a detailed description plus all text on it.",
    },
    {
        "id": "camera",
        "label": "Camera",
        "description": (
            "Take a camera snapshot of one of the user's rooms and answer a question "
            "about what the camera sees (e.g. whether a cat is in the room). "
            "To compare several rooms, plan ONE step per room with the room name "
            "in the goal."
        ),
        "checkpoint_hint": "Name the room and the question the analysis must answer.",
    },
    {
        "id": "crawl",
        "label": "Site study",
        "description": (
            "Deeply study a website (it contains a URL): fetch its pages, index them "
            "as a document and answer questions about the site. Pair with a following "
            "llm step that composes the answer from the collected material."
        ),
        "checkpoint_hint": "Name the site and the question the study must answer.",
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
        "description": (
            "Edit one image attached to the task according to an instruction. To edit "
            "several images, plan ONE step per image with that image's file name in the "
            "goal: the step edits the image its goal names (the first attached one when "
            "the goal names none)."
        ),
        "checkpoint_hint": "Name this image's file in the goal and the change that must be visible.",
    },
    {
        "id": "video_gen",
        "label": "Video generation",
        "description": "Generate a short video from a text prompt.",
        "checkpoint_hint": "Describe the scene and motion the video must show.",
    },
]


def get_capabilities() -> list[dict[str, str]]:
    return [dict(c) for c in CAPABILITIES]
