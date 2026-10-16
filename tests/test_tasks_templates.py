# tests/test_tasks_templates.py
"""Format-safety guards for the Tasks subsystem prompt template pairs."""

import re
from pathlib import Path

TEMPLATES = ("task_plan", "task_step", "task_checkpoint", "task_final")
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

EXPECTED_PLACEHOLDERS: dict[str, set[str]] = {
    "task_plan": {
        "task_request",
        "capabilities_section",
        "slm_facts",
        "deep_analysis_note",
        "response_language",
        "attachments_section",
        "current_time",
    },
    "task_step": {
        "task_request",
        "plan",
        "step_goal",
        "step_tool",
        "step_checkpoint",
        "previous_results",
        "user_answers",
        "slm_facts",
        "response_language",
        "response_style",
        "current_time",
    },
    "task_checkpoint": {"step_goal", "step_checkpoint", "step_result", "response_language"},
    "task_final": {
        "task_request",
        "plan",
        "results_digest",
        "slm_facts",
        "response_language",
        "response_style",
        "current_time",
    },
}

DUMMY_VALUES: dict[str, str] = {
    "task_request": "Prepare a currency rate report",
    "capabilities_section": "- llm: reasoning model\n- search: web search",
    "deep_analysis_note": "",
    "slm_facts": "",
    "response_language": "Russian",
    "response_style": "neutral",
    "plan": "1. Step one\n2. Step two",
    "step_goal": "Do step one",
    "step_tool": "llm",
    "step_checkpoint": "The result contains the final number",
    "previous_results": "Step 1: done",
    "user_answers": "",
    "step_result": "Got 12",
    "results_digest": "Step 1: done\nStep 2: got 12",
    "attachments_section": "Attached images: 2 (a.jpg, b.jpg).",
    "current_time": "15 October 2026, time 12:00:00 Thursday (UTC+3)",
}


def _load(name: str, lang: str) -> str:
    path = PROMPTS_DIR / lang / f"{name}.template"
    assert path.is_file(), f"missing template: {path.relative_to(PROMPTS_DIR.parent)}"
    return path.read_text(encoding="utf-8")


def test_template_pairs_exist():
    for name in TEMPLATES:
        for lang in ("ru", "en"):
            _load(name, lang)


def test_placeholder_sets_match_brief():
    for name, expected in EXPECTED_PLACEHOLDERS.items():
        for lang in ("ru", "en"):
            found = set(PLACEHOLDER_RE.findall(_load(name, lang)))
            extra = sorted(found - expected)
            missing = sorted(expected - found)
            assert found == expected, f"{name}/{lang}: extra={extra} missing={missing}"


def test_ru_en_placeholder_sets_identical():
    for name in TEMPLATES:
        ru = set(PLACEHOLDER_RE.findall(_load(name, "ru")))
        en = set(PLACEHOLDER_RE.findall(_load(name, "en")))
        assert ru == en, f"{name}: ru={sorted(ru)} en={sorted(en)}"


def test_templates_format_cleanly():
    """str.format with all known placeholders must not raise (guards unescaped braces)."""
    for name in TEMPLATES:
        for lang in ("ru", "en"):
            _load(name, lang).format(**DUMMY_VALUES)


def test_templates_do_not_use_skills_section():
    for name in TEMPLATES:
        for lang in ("ru", "en"):
            assert "{skills_section}" not in _load(name, lang), f"{name}/{lang} uses skills_section"


def test_plan_template_has_current_time_and_attachments():
    """The planner must SEE the current date/time and the task's attachments:
    a calendar question routed to web search because the prompt had no date,
    and 4 attached images still got a «Прикрепите изображения…» question
    because the prompt never named them."""
    for lang in ("ru", "en"):
        src = _load("task_plan", lang)
        assert "{current_time}" in src, f"task_plan/{lang}: missing current_time"
        assert "{attachments_section}" in src, f"task_plan/{lang}: missing attachments_section"


def test_plan_template_dates_need_no_web_search():
    """Planning rule: the date/time is given in the input — calendar questions
    are computed by llm/calculate, not searched on the web."""
    for lang in ("ru", "en"):
        src = _load("task_plan", lang).lower()
        word = "поиск" if lang == "ru" else "search"
        assert word in src, f"task_plan/{lang}: the no-search rule names the capability"


def test_answer_templates_forbid_latex_math():
    """User-facing task answers must use plain-text math: the chat ships no
    LaTeX renderer, so ``$2 + 2 \\times 2$`` reached the user as raw markup."""
    for name in ("task_step", "task_final"):
        for lang in ("ru", "en"):
            src = _load(name, lang)
            assert "LaTeX" in src, f"{name}/{lang}: missing the plain-text-math rule"


def test_plan_template_binds_media_steps_to_their_source_step():
    """The planner knows the ``source_step`` field: a media step consuming a
    prior step's artifact names that producer step (live defect: two video
    steps in one task could not name their different source images, so both
    were made from the same «latest» image)."""
    for lang in ("ru", "en"):
        src = _load("task_plan", lang)
        assert "source_step" in src, f"task_plan/{lang}: missing the source_step rule"
        assert '"source_step"' in src, f"task_plan/{lang}: the JSON schema carries the source_step field"
