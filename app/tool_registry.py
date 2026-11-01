"""The background-task capabilities, as one manifest.

Before this module a capability lived in five places: the planner's prose in
``app/tasks/tools.py``, its badge beside that, and three literal tables in
``app/tasks/executor.py`` (``_SUBTASK_TOOLS``, ``_SUBTASK_TYPE``, ``_MEDIA_TOOLS``).
Adding one meant editing all five and hoping they agreed; nothing checked.

Every view below is derived from :data:`TOOL_SPECS`, so the tables cannot drift
apart. The module is deliberately flat data — the point is one place to look, not
a plugin system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.utils import N_


@dataclass(frozen=True)
class ToolSpec:
    """One capability, and everything the rest of the system derives from it.

    ``exec``      — ``direct`` runs in-process on the executor thread,
                    ``queue`` is enqueued as a sub-task through the GPU queue.
    ``task_type`` — the queue's task type; required for ``queue``, forbidden for
                    ``direct`` (both asserted by the tests).
    ``media``     — the step is expected to leave a file behind, which is what
                    gives it a candidate list and the repair loop.
    ``label`` / ``description`` / ``checkpoint_hint`` — the planner's prose.
    ``badge``     — the emoji and msgid ``app/routes/tasks.py`` renders.
    """

    id: str
    exec: str
    badge: tuple[str, str] | None = None
    task_type: str | None = None
    media: bool = False
    label: str | None = None
    description: str | None = None
    checkpoint_hint: str | None = None
    # The JSON Schema a model calls the tool with. Only sandbox tools carry one
    # today: a task capability is named in a plan, not called by the model.
    schema: dict[str, Any] | None = field(default=None)


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="llm",
        exec="direct",
        label=N_("Reasoning model"),
        description=N_("Think, write, analyze, derive, write code without fresh internet data."),
        checkpoint_hint=N_("State the concrete artifact or answer the reasoning must produce."),
    ),
    ToolSpec(
        id="search",
        exec="direct",
        badge=("🔎", N_("Web search")),
        label=N_("Web search"),
        description=N_("Search the internet for fresh data (rates, news, current facts)."),
        checkpoint_hint=N_("Name the fact or data the search must confirm."),
    ),
    ToolSpec(
        id="research",
        exec="direct",
        badge=("🔬", N_("Research")),
        label=N_("Research"),
        description=N_(
            "Research a question across the web and the user's documents in several rounds: "
            "search, open what looks relevant, refine the query. Use it when one search is "
            "not enough and the answer must be cross-checked against more than one source."
        ),
        checkpoint_hint=N_("Name the facts the research must establish and agree on."),
    ),
    ToolSpec(
        id="rag",
        exec="direct",
        badge=("📄", N_("Document search")),
        label=N_("Document search"),
        description=N_(
            "Search the user's own documents and return the relevant fragments. "
            "Pair with a following llm step that composes the answer from them."
        ),
        checkpoint_hint=N_("Name the data the search must find in the documents."),
    ),
    ToolSpec(
        id="history",
        exec="direct",
        badge=("🕘", N_("Conversation history")),
        label=N_("Conversation history"),
        description=N_(
            "Search the user's past conversations for a topic and return matching "
            "fragments (what was discussed earlier). Pair with a following llm step "
            "that composes the answer from them."
        ),
        checkpoint_hint=N_("Name the topic the past conversation must contain."),
    ),
    ToolSpec(
        id="vision",
        exec="queue",
        task_type="vision_task",
        badge=("🖼️", N_("Image analysis")),
        label=N_("Image analysis"),
        description=N_(
            "Extract data from ONE image attached to the task with the multimodal "
            "model: a detailed description of everything visible plus all text on the "
            "image. It does not answer the task question — pair it with a following "
            "llm step that composes the answer from the extraction. To analyze "
            "several images, plan ONE step per image with that image's file name in "
            "the goal: the step reads only the image its goal names. Use only when "
            "images are attached to the task."
        ),
        checkpoint_hint=N_(
            "Name this image's file in the goal and require a detailed description plus all text on it."
        ),
    ),
    ToolSpec(
        id="camera",
        exec="queue",
        task_type="camera_task",
        badge=("📷", N_("Camera")),
        label=N_("Camera"),
        description=N_(
            "Take a camera snapshot of one of the user's rooms and answer a question "
            "about what the camera sees (e.g. whether a cat is in the room). "
            "To compare several rooms, plan ONE step per room with the room name "
            "in the goal."
        ),
        checkpoint_hint=N_("Name the room and the question the analysis must answer."),
    ),
    ToolSpec(
        id="crawl",
        exec="queue",
        task_type="crawl_task",
        badge=("🕸️", N_("Site study")),
        label=N_("Site study"),
        description=N_(
            "Deeply study a website (it contains a URL): fetch its pages, index them "
            "as a document and answer questions about the site. Pair with a following "
            "llm step that composes the answer from the collected material."
        ),
        checkpoint_hint=N_("Name the site and the question the study must answer."),
    ),
    ToolSpec(
        id="calculate",
        exec="direct",
        badge=("🧮", N_("Calculator")),
        label=N_("Calculator"),
        description=N_("Evaluate a math expression safely (arithmetic and math functions)."),
        checkpoint_hint=N_("State the expression and its expected exact result."),
    ),
    ToolSpec(
        id="rlm",
        exec="queue",
        task_type="rlm_analysis",
        badge=("🔬", N_("Deep analysis")),
        label=N_("Deep analysis"),
        description=N_("Multi-step deep analysis over selected documents, images and web pages."),
        checkpoint_hint=N_("Name the documents and the question the analysis must answer."),
    ),
    ToolSpec(
        id="continuity",
        exec="direct",
        badge=("📐", N_("Visual continuity")),
        label=N_("Visual continuity"),
        description=N_(
            "Extract the visual continuity sheet: one style plus the fixed look "
            "of every character, place and prop named in the task, as one JSON "
            "object. Plan it before the first image generation step when the "
            "task produces several images."
        ),
        checkpoint_hint=N_("Output the sheet as valid JSON."),
    ),
    ToolSpec(
        id="image_gen",
        exec="queue",
        task_type="image_gen",
        media=True,
        badge=("🖼️", N_("Image generation")),
        label=N_("Image generation"),
        description=N_("Generate an image from a text prompt."),
        checkpoint_hint=N_("Describe the subject the generated image must show."),
    ),
    ToolSpec(
        id="image_edit",
        exec="queue",
        task_type="api_image_edit",
        media=True,
        badge=("🎨", N_("Image editing")),
        label=N_("Image editing"),
        description=N_(
            "Edit one image according to an instruction: the attached image the "
            "goal names (one step per image; the first attached one when the goal "
            "names none), or the artifact of an earlier step when its seq is set "
            'in "source_step" (no attachment edit). Without any source the step fails.'
        ),
        checkpoint_hint=N_("Name this image's file in the goal and the change that must be visible."),
    ),
    ToolSpec(
        id="video_gen",
        exec="queue",
        task_type="video",
        media=True,
        badge=("🎬", N_("Video generation")),
        label=N_("Video generation"),
        description=N_(
            'Generate a short video from a text prompt; with "source_step" (or '
            "any earlier step-produced image) it animates that image instead."
        ),
        checkpoint_hint=N_("Describe the scene and motion the video must show."),
    ),
)


def spec_for(tool_id: str) -> ToolSpec | None:
    """The manifest entry for a capability id, or None."""
    for spec in TOOL_SPECS:
        if spec.id == tool_id:
            return spec
    return None


# The tools a sandboxed actor loop (deep analysis) may call. They are not task
# capabilities: a planner cannot name them, and they never reach ``CAPABILITIES``.
RLM_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="python",
        exec="sandbox",
        description="Execute Python over the corpus. Use context[filename] to access text.",
        schema={
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python code to execute."}},
            "required": ["code"],
        },
    ),
    ToolSpec(
        id="llm",
        exec="sandbox",
        description="Ask a sub-model a question about a specific text fragment.",
        schema={
            "type": "object",
            "properties": {"prompt": {"type": "string"}, "text": {"type": "string"}},
            "required": ["prompt"],
        },
    ),
    ToolSpec(
        id="web_fetch",
        exec="sandbox",
        description="Search the web and fetch readable page content.",
        schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    ),
    ToolSpec(
        id="final",
        exec="sandbox",
        description="Finish the analysis and return the final answer.",
        schema={"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
    ),
)


def sandbox_tool_definitions() -> list[dict[str, Any]]:
    """The sandbox tools in OpenAI shape, for the deep-analysis actor loop."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.id,
                "description": spec.description or spec.id,
                "parameters": spec.schema or {"type": "object", "properties": {}},
            },
        }
        for spec in RLM_TOOL_SPECS
    ]


def capabilities() -> list[dict[str, str]]:
    """The planner's prose view, in manifest order."""
    return [
        {
            "id": spec.id,
            "label": spec.label or "",
            "description": spec.description or "",
            "checkpoint_hint": spec.checkpoint_hint or "",
        }
        for spec in TOOL_SPECS
    ]


def badges() -> dict[str, tuple[str, str]]:
    """The UI badge per capability that has one."""
    return {spec.id: spec.badge for spec in TOOL_SPECS if spec.badge}


def subtask_tools() -> set[str]:
    """Capabilities that run through the queue."""
    return {spec.id for spec in TOOL_SPECS if spec.exec == "queue"}


def subtask_types() -> dict[str, str]:
    """Capability id to queue task type."""
    return {spec.id: spec.task_type for spec in TOOL_SPECS if spec.exec == "queue" and spec.task_type}


def media_tools() -> set[str]:
    """Capabilities whose step is expected to leave a file behind."""
    return {spec.id for spec in TOOL_SPECS if spec.media}
