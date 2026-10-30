"""A task step that researches: search, open, refine — inside one step.

The step loop hands every capability one job. ``search`` answers with one query
and ``llm`` writes one answer; neither can correct itself when the first result
is thin. This capability runs the shared engine loop
(:func:`app.engine.loop.run_tool_loop`) over the read-only half of the chat
catalogue, so the model can search, open a page, notice it needs a better query
and try again — while the step still costs one plan entry and one checkpoint.

Budget: four model rounds. A task step is a promise to the plan, not a research
project; a fifth round has never been worth an extra minute of GPU time in the
queue that serialises everything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.engine.loop import Turn, run_tool_loop
from app.engine.session import ChatSession

if TYPE_CHECKING:
    from app.tasks.runner import DirectStepContext

# Everything here only reads. A task step must not be able to place an image,
# restart a container or talk to a camera through a loop it did not plan for.
RESEARCH_TOOLS: tuple[str, ...] = (
    "web_search",
    "read_page",
    "rag_search",
    "history_search",
    "get_current_time",
)

RESEARCH_MAX_ROUNDS = 4


def run_research(context: DirectStepContext) -> str:
    """Run the tool loop for one step and return its answer.

    Raises :class:`StepAborted` when the step is cancelled, so the executor's
    existing preemption path handles it exactly as it handles a single call.
    """
    from app.tasks import runner
    from app.tools import execute_tool, get_tool_definitions, parse_tool_arguments

    app = context.app
    step = context.step
    lang = context.lang
    cancel_check = context.cancel_check

    prompt = runner.build_step_prompt(
        app,
        step,
        context.context_digest,
        lang,
        context.response_style,
        context.task_request,
        context.plan_text,
        context.user_answers,
        context.user_id,
    )
    tools = [item for item in get_tool_definitions(lang) if item["function"]["name"] in RESEARCH_TOOLS]
    if not tools:
        raise RuntimeError("no research tool is available on this installation")

    session = ChatSession(
        messages=[{"role": "user", "content": prompt}],
        is_cancelled=cancel_check,
        # The UI shows the step, not its individual tool calls; the step's own
        # progress event is what the user needs. A per-tool feed here would add
        # a stream of events for work the plan never promised.
        publish=lambda _event, _payload: None,
        report_stage=lambda _stage: None,
    )
    schemas = {item["function"]["name"]: item["function"].get("parameters") for item in tools}
    usage_total: dict[str, int] = {}

    def generate(tool_choice: str | None) -> Turn:
        text, usage = runner._call_reasoning(
            app,
            "",
            cancel_check,
            lang=lang,
            tools=tools,
            messages=session.messages,
            tool_choice=tool_choice,
        )
        for key, value in (usage or {}).items():
            if isinstance(value, (int, float)):
                usage_total[key] = usage_total.get(key, 0) + int(value)
        calls = runner.last_tool_calls()
        if runner._is_error_text(text or ""):
            return Turn(error=text)
        return Turn(text=text or "", tool_calls=calls)

    def execute(tool_calls: list[dict[str, Any]], assistant_text: str) -> str:
        session.append_assistant(assistant_text, tool_calls)
        last_result = ""
        for call in tool_calls:
            call_id = str(call.get("id", ""))
            function = call.get("function") or {}
            name = str(function.get("name", ""))
            arguments, usable = parse_tool_arguments(function.get("arguments"), schemas.get(name))
            if not usable or name not in schemas:
                # The model is told what went wrong and can try a different call;
                # an unparsable one must never run with empty arguments.
                session.append_tool(call_id, _refusal(lang))
                continue
            result = execute_tool(name, arguments, {"app": app, "user_id": context.user_id, "lang": lang})
            last_result = result
            session.append_tool(call_id, result)
        return last_result

    result = run_tool_loop(
        max_iterations=RESEARCH_MAX_ROUNDS,
        generate=generate,
        execute=execute,
        is_cancelled=cancel_check,
        on_final_round=lambda: None,
    )
    runner._last_call_usage.value = usage_total
    if result.cancelled:
        raise runner.StepAborted("step cancelled during research")
    if result.error:
        return result.error
    return result.text.strip() or result.last_tool_result.strip()


def _refusal(lang: str) -> str:
    from flask_babel import force_locale
    from flask_babel import gettext as _

    with force_locale(lang):
        return str(_("The tool call was rejected: its arguments did not match the tool."))
