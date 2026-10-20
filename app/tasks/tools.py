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

    The badge names and the capability registry fields live in module-level
    dicts, so an ``_()`` call would be
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
    "continuity": ("📐", N_("Visual continuity")),
    "image_gen": ("🖼️", N_("Image generation")),
    "image_edit": ("🎨", N_("Image editing")),
    "video_gen": ("🎬", N_("Video generation")),
}

CAPABILITIES: list[dict[str, str]] = [
    {
        "id": "llm",
        "label": N_("Reasoning model"),
        "description": N_("Think, write, analyze, derive, write code without fresh internet data."),
        "checkpoint_hint": N_("State the concrete artifact or answer the reasoning must produce."),
    },
    {
        "id": "search",
        "label": N_("Web search"),
        "description": N_("Search the internet for fresh data (rates, news, current facts)."),
        "checkpoint_hint": N_("Name the fact or data the search must confirm."),
    },
    {
        "id": "rag",
        "label": N_("Document search"),
        "description": N_(
            "Search the user's own documents and return the relevant fragments. "
            "Pair with a following llm step that composes the answer from them."
        ),
        "checkpoint_hint": N_("Name the data the search must find in the documents."),
    },
    {
        "id": "history",
        "label": N_("Conversation history"),
        "description": N_(
            "Search the user's past conversations for a topic and return matching "
            "fragments (what was discussed earlier). Pair with a following llm step "
            "that composes the answer from them."
        ),
        "checkpoint_hint": N_("Name the topic the past conversation must contain."),
    },
    {
        "id": "vision",
        "label": N_("Image analysis"),
        "description": N_(
            "Extract data from ONE image attached to the task with the multimodal "
            "model: a detailed description of everything visible plus all text on the "
            "image. It does not answer the task question — pair it with a following "
            "llm step that composes the answer from the extraction. To analyze "
            "several images, plan ONE step per image with that image's file name in "
            "the goal: the step reads only the image its goal names. Use only when "
            "images are attached to the task."
        ),
        "checkpoint_hint": N_(
            "Name this image's file in the goal and require a detailed description plus all text on it."
        ),
    },
    {
        "id": "camera",
        "label": N_("Camera"),
        "description": N_(
            "Take a camera snapshot of one of the user's rooms and answer a question "
            "about what the camera sees (e.g. whether a cat is in the room). "
            "To compare several rooms, plan ONE step per room with the room name "
            "in the goal."
        ),
        "checkpoint_hint": N_("Name the room and the question the analysis must answer."),
    },
    {
        "id": "crawl",
        "label": N_("Site study"),
        "description": N_(
            "Deeply study a website (it contains a URL): fetch its pages, index them "
            "as a document and answer questions about the site. Pair with a following "
            "llm step that composes the answer from the collected material."
        ),
        "checkpoint_hint": N_("Name the site and the question the study must answer."),
    },
    {
        "id": "calculate",
        "label": N_("Calculator"),
        "description": N_("Evaluate a math expression safely (arithmetic and math functions)."),
        "checkpoint_hint": N_("State the expression and its expected exact result."),
    },
    {
        "id": "rlm",
        "label": N_("Deep analysis"),
        "description": N_("Multi-step deep analysis over selected documents, images and web pages."),
        "checkpoint_hint": N_("Name the documents and the question the analysis must answer."),
    },
    {
        "id": "continuity",
        "label": N_("Visual continuity"),
        "description": N_(
            "Extract the visual continuity sheet: one style plus the fixed look "
            "of every character, place and prop named in the task, as one JSON "
            "object. Plan it before the first image generation step when the "
            "task produces several images."
        ),
        "checkpoint_hint": N_("Output the sheet as valid JSON."),
    },
    {
        "id": "image_gen",
        "label": N_("Image generation"),
        "description": N_("Generate an image from a text prompt."),
        "checkpoint_hint": N_("Describe the subject the generated image must show."),
    },
    {
        "id": "image_edit",
        "label": N_("Image editing"),
        "description": N_(
            "Edit one image according to an instruction: the attached image the "
            "goal names (one step per image; the first attached one when the goal "
            "names none), or the artifact of an earlier step when its seq is set "
            'in "source_step" (no attachment edit). Without any source the step fails.'
        ),
        "checkpoint_hint": N_("Name this image's file in the goal and the change that must be visible."),
    },
    {
        "id": "video_gen",
        "label": N_("Video generation"),
        "description": N_(
            'Generate a short video from a text prompt; with "source_step" (or '
            "any earlier step-produced image) it animates that image instead."
        ),
        "checkpoint_hint": N_("Describe the scene and motion the video must show."),
    },
]


def get_capabilities() -> list[dict[str, str]]:
    return [dict(c) for c in CAPABILITIES]
