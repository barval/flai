# tests/test_tasks_templates.py
"""Format-safety guards for the Tasks subsystem prompt template pairs."""

import re
from pathlib import Path

TEMPLATES = ("task_plan", "task_step", "task_checkpoint", "task_checkpoint_media", "task_final", "task_replan")
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

EXPECTED_PLACEHOLDERS: dict[str, set[str]] = {
    "task_plan": {
        "task_request",
        "capabilities_section",
        "slm_facts",
        "deep_analysis_note",
        "document_tool_rule",
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
    "task_checkpoint_media": {
        "expected_reference",
        "step_goal",
        "step_checkpoint",
        "generation_prompt",
        "artifact_description",
        "response_language",
    },
    "task_final": {
        "task_request",
        "plan",
        "results_digest",
        "slm_facts",
        "response_language",
        "response_style",
        "current_time",
    },
    "task_replan": {
        "task_request",
        "response_language",
        "results_digest",
        "user_answers",
        "remaining_plan",
        "capabilities_section",
        "attachments_section",
        "document_tool_rule",
        "deep_analysis_note",
        "current_time",
    },
}

DUMMY_VALUES: dict[str, str] = {
    "task_request": "Prepare a currency rate report",
    "capabilities_section": "- llm: reasoning model\n- search: web search",
    "deep_analysis_note": "",
    "document_tool_rule": "- To find data in the documents, use the rag capability.",
    "slm_facts": "",
    "response_language": "Russian",
    "response_style": "neutral",
    "plan": "1. Step one\n2. Step two",
    "step_goal": "Do step one",
    "step_tool": "llm",
    "step_checkpoint": "The result contains the final number",
    "previous_results": "Step 1: done",
    "user_answers": "Q: Anything else?\nA: draw them all",
    "remaining_plan": "3. [image_gen] Draw scene three",
    "step_result": "Got 12",
    "expected_reference": "Part six: a boy and a mole in a tunnel",
    "generation_prompt": "a dense autumn forest at dusk",
    "artifact_description": "a forest at dusk, no characters",
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


def test_plan_template_pins_the_answer_language():
    """Every plan text field is pinned to ``{response_language}``: at planner
    temperature 0.2 the same Russian request produced a Russian plan once and
    an English one half an hour later (live defect: the English goals reached
    the user and became the query of every media step)."""
    for lang in ("ru", "en"):
        src = _load("task_plan", lang)
        assert "{response_language}" in src, f"task_plan/{lang}: language placeholder missing"
        marker = "языка описаний способностей" if lang == "ru" else "language of the capability descriptions"
        assert marker in src, f"task_plan/{lang}: the language rule must outrank the capabilities block"
        assert "checkpoint_criteria" in src, f"task_plan/{lang}: the rule names the plan's text fields"


def test_checkpoint_template_carries_a_single_language():
    """The Russian checkpoint template once had the whole English template
    appended (45 vs 24 lines): every Russian verification call read a complete
    English copy of the rules, and the step result was echoed twice."""
    for lang in ("ru", "en"):
        src = _load("task_checkpoint", lang)
        for placeholder in ("{step_goal}", "{step_checkpoint}", "{step_result}"):
            assert src.count(placeholder) == 1, f"task_checkpoint/{lang}: {placeholder} appears more than once"
    ru = _load("task_checkpoint", "ru")
    assert "# INPUT" not in ru and "Step goal:" not in ru, "the ru template must not carry the English block"
    en = _load("task_checkpoint", "en")
    assert "# ВХОДНЫЕ ДАННЫЕ" not in en and "Цель шага:" not in en, "the en template must not carry the Russian block"


def test_checkpoint_template_accepts_a_media_description():
    """The verifier is text-only: an image step's criterion is judged by the
    description of the produced file (the executor appends it to the verifier
    input), so the template must say so — otherwise every image step is
    rejected with "the result provides no image or its description"."""
    for lang in ("ru", "en"):
        src = _load("task_checkpoint", lang)
        marker = "описание содержимого файла" if lang == "ru" else "description of the file's content"
        assert marker in src, f"task_checkpoint/{lang}: missing the media-description rule"


def test_plan_template_requires_the_source_content_in_a_media_goal():
    """A media step that illustrates part of an earlier result must carry that
    part's complete description into goal and name concrete expected content in
    its criterion — a content-free goal/criterion made every later image a copy
    of the first one (the verifier had nothing to check)."""
    for lang in ("ru", "en"):
        src = _load("task_plan", lang).lower()
        marker = "описание этой части" if lang == "ru" else "description of that part"
        assert marker in src, f"task_plan/{lang}: missing the source-content rule"
        assert "source_step" in src
        assert "checkpoint_criteria" in src


def test_replan_template_requires_the_source_content_in_a_media_goal():
    """The re-planner rewrites the remaining tail from a truncated digest; it
    must copy the planned part's description into the new goal too."""
    for lang in ("ru", "en"):
        src = _load("task_replan", lang).lower()
        marker = "описание этой части" if lang == "ru" else "description of that part"
        assert marker in src, f"task_replan/{lang}: missing the source-content rule"


def test_media_checkpoint_template_handles_an_empty_reference_and_an_edit():
    """Two normal cases must not be judged as failures: a media step with no
    source binding (empty reference) and an edit step whose goal is to CHANGE
    the reference image — otherwise a correct artifact is rejected three times."""
    for lang in ("ru", "en"):
        src = _load("task_checkpoint_media", lang).lower()
        if lang == "ru":
            assert "раздел эталона пуст" in src
            assert "изменить эталон" in src
        else:
            assert "reference section is empty" in src
            assert "requires changing the reference" in src
