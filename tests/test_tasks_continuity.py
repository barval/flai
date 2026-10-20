# tests/test_tasks_continuity.py
"""Visual continuity sheet tests: pure helpers + the continuity direct-step runner."""

import json
import re
from unittest.mock import patch

from app.tasks.continuity import (
    render_sheet,
    sanitize_reference_markers,
    select_cards,
)
from app.tasks.runner import run_direct_step


def test_strips_line_range_markers():
    assert sanitize_reference_markers(
        "Illyustratsiya k Scene 8 (строки ~540–621): Баба Яга у избушки") \
        == "Illyustratsiya k Scene 8: Баба Яга у избушки"


def test_stripped_line_formats():
    for frag in ("(строки 438–500)", "(lines 100-150)", "строка 42",
                 "(источник: стр. 3)", "(source: page 3)"):
        assert frag not in sanitize_reference_markers(f"Нарисуй сцену {frag}")


def test_keeps_scene_number_and_quotes():
    s = 'Нарисуй вывеску "Открыто" к Сцене 8'
    assert sanitize_reference_markers(s) == s


def test_select_cards_matches_lemma():
    bible = {"style": "s", "characters": [], "places": [],
             "props": [{"id": "owl", "aliases": ["сова"], "look": "large brown owl"}]}
    assert select_cards(bible, "Разговор сов на ветке")[0]["id"] == "owl"


def test_select_cards_no_match_returns_empty():
    assert select_cards({"style": "s", "characters": [], "places": [], "props": []},
                        "её появление") == []


def test_select_cards_matches_multi_word_alias_lemma():
    # Russian strings are test data (declined multi-word alias).
    bible = {"style": "s", "characters": [], "places": [],
             "props": [{"id": "hut", "aliases": ["Баба Яга"], "look": "old witch"}]}
    assert select_cards(bible, "Нарисуй Бабу Ягу у избушки")[0]["id"] == "hut"


def test_select_cards_multi_word_alias_requires_all_tokens():
    bible = {"style": "s", "characters": [], "places": [],
             "props": [{"id": "hut", "aliases": ["Баба Яга"], "look": "old witch"}]}
    assert select_cards(bible, "Яга ушла") == []


def test_select_cards_multi_word_alias_case_insensitive():
    bible = {"style": "s", "characters": [], "places": [],
             "props": [{"id": "hut", "aliases": ["Баба Яга"], "look": "old witch"}]}
    assert select_cards(bible, "бabay- нет, а вот БАБА ЯГА летит")[0]["id"] == "hut"


def test_sanitizer_removes_citation_up_to_200_chars():
    payload = "источник: " + "x" * 190
    assert sanitize_reference_markers(f"Сцена 8 ({payload}) нарисована") == "Сцена 8 нарисована"


def test_sanitizer_keeps_oversized_citation_payload():
    payload = "источник: " + "x" * 201
    text = f"Сцена 8 ({payload})"
    assert sanitize_reference_markers(text) == text


def test_render_sheet_shape():
    text = render_sheet({"style": "storybook, warm colors",
                         "characters": [{"id": "zhenya", "aliases": ["Женя"], "look": "boy, freckles"}],
                         "places": [], "props": []},
                        goal="Нарисуй Женю у леса")
    assert text.startswith("CONTINUITY (mandatory for this task, keep identical across all images):")
    assert "Style: storybook, warm colors" in text
    assert "Zhenya: boy, freckles" in text
    assert text.count("\n") <= 6  # compact


# ── Runner: the continuity direct step (app/tasks/runner.py) ───────────


def _sheet_json() -> str:
    """A valid continuity sheet as the extractor model would emit it."""
    return json.dumps(
        {
            "style": "children's book illustration, warm colors",
            "characters": [
                {"id": "zhenya", "aliases": ["Женя", "Женька"], "look": "6-year-old boy, messy chestnut hair, freckles"}
            ],
            "places": [
                {"id": "forest", "aliases": ["лес"], "look": "pine forest at sunset, golden light through tall trunks"}
            ],
            "props": [
                {"id": "owl", "aliases": ["сова"], "look": "large brown owl with yellow eyes"}
            ],
        },
        ensure_ascii=False,
    )


def _cont_step(goal="Составь визуальную библию задачи", extra=None):
    step = {"id": "s1", "kind": "direct", "tool": "continuity", "goal": goal, "checkpoint_criteria": "c"}
    if extra is not None:
        step["extra"] = extra
    return step


def _passed_checkpoint():
    return {"verdict": True, "explanation": "ok", "exec_seconds": 0.1}


def test_continuity_step_valid_json_passes(test_app):
    sheet = _sheet_json()
    with (
        patch("app.tasks.runner._call_reasoning", return_value=sheet),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        out = run_direct_step(test_app, _cont_step(), context_digest="", cancel_check=lambda: False)
    assert out["status"] == "passed"
    assert out["result"] == sheet
    assert out["checkpoint"]["verdict"] is True


def test_continuity_step_prose_is_error(test_app):
    with patch("app.tasks.runner._call_reasoning", return_value="Вот визуальная библия задачи."):
        out = run_direct_step(test_app, _cont_step(), context_digest="", cancel_check=lambda: False)
    assert out["status"] == "error" and out["checkpoint"] is None
    assert out["result"].startswith("continuity"), "the parse failure reason must surface, not a bare tool error"


def test_continuity_step_llm_error_surfaces_as_itself(test_app):
    """A persistent LLM error reaches the step result as itself, not masked
    as a JSON parse failure (parity with the llm tool's error path)."""
    err = "⚠️ Failed to get an answer from the reasoning model."
    with patch("app.tasks.runner._call_reasoning", return_value=err):
        out = run_direct_step(test_app, _cont_step(), context_digest="", cancel_check=lambda: False)
    assert out["status"] == "error" and out["checkpoint"] is None
    assert out["result"] == err


def test_continuity_step_oversized_sheet_is_not_truncated(test_app):
    """An over-budget sheet passes WHOLE through the runner (spec §8: the
    corrective re-extraction and the accept-whole ruling live in the
    extraction core, never a cut at this level)."""
    sheet = _sheet_json()  # far above a 10-char budget
    test_app.config["TASK_CONTINUITY_MAX_CHARS"] = 10
    with (
        patch("app.tasks.runner._call_reasoning", return_value=sheet),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        out = run_direct_step(test_app, _cont_step(), context_digest="", cancel_check=lambda: False)
    assert out["status"] == "passed"
    assert out["result"] == sheet, "the raw sheet must arrive whole — truncation is not the runner's call"


def test_continuity_prompt_contains_corpus_and_no_placeholders(test_app):
    captured = {}

    def fake_call(app, prompt, cancel_check, lang="ru"):
        captured["prompt"] = prompt
        return _sheet_json()

    with (
        patch("app.tasks.runner._call_reasoning", side_effect=fake_call),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        run_direct_step(
            test_app,
            _cont_step(),
            context_digest="Женя у леса, сова на ветке",
            cancel_check=lambda: False,
            task_request="Разбей сказку на сцены и нарисуй иллюстрации",
            plan_text="1. выписать сцены; 2. нарисовать Женю; 3. нарисовать сову",
            lang="ru",
        )
    prompt = captured["prompt"]
    assert "Разбей сказку" in prompt
    assert "Женя у леса" in prompt
    assert "нарисовать Женю" in prompt
    assert not re.search(r"\{[a-z_]+\}", prompt), f"unsubstituted placeholder in prompt: {prompt}"


def test_continuity_prompt_strips_reference_markers_from_corpus(test_app):
    """Line-range markers in the corpus (plan goals, previous results) never
    reach the extractor prompt — the scene content itself does."""
    captured = {}

    def fake_call(app, prompt, cancel_check, lang="ru"):
        captured["prompt"] = prompt
        return _sheet_json()

    with (
        patch("app.tasks.runner._call_reasoning", side_effect=fake_call),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        run_direct_step(
            test_app,
            _cont_step(),
            context_digest="Сцена 8 (строки ~438–500): Баба Яга — второе появление",
            cancel_check=lambda: False,
            plan_text="Нарисуй иллюстрацию к Сцене 8 (lines 200-260)",
            lang="ru",
        )
    prompt = captured["prompt"]
    assert "438" not in prompt, "the corpus line-range marker must not reach the extractor"
    assert "200-260" not in prompt
    assert "Баба Яга" in prompt, "the scene content itself survives marker removal"


def test_continuity_prompt_renders_existing_sheet_merge_section(test_app):
    """An existing sheet in step extra switches the prompt to merge mode."""
    captured = {}

    def fake_call(app, prompt, cancel_check, lang="ru"):
        captured["prompt"] = prompt
        return _sheet_json()

    existing = {
        "style": "watercolor",
        "characters": [{"id": "cat", "aliases": ["кот"], "look": "a black cat with a white spot"}],
        "places": [],
        "props": [],
    }
    with (
        patch("app.tasks.runner._call_reasoning", side_effect=fake_call),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        run_direct_step(
            test_app,
            _cont_step(extra={"existing_sheet": existing}),
            context_digest="",
            cancel_check=lambda: False,
            lang="ru",
        )
    assert "watercolor" in captured["prompt"]
    assert "a black cat with a white spot" in captured["prompt"]

    captured.clear()
    with (
        patch("app.tasks.runner._call_reasoning", side_effect=fake_call),
        patch("app.tasks.runner.run_checkpoint", return_value=_passed_checkpoint()),
    ):
        run_direct_step(test_app, _cont_step(), context_digest="", cancel_check=lambda: False, lang="ru")
    assert "watercolor" not in captured["prompt"], "no existing sheet — no merge section content"


def test_continuity_registered_in_badges_and_capabilities():
    from app.tasks.tools import CAPABILITIES, TOOL_BADGES

    assert TOOL_BADGES["continuity"] == ("📐", "Visual continuity")
    cap = next(c for c in CAPABILITIES if c["id"] == "continuity")
    assert cap["checkpoint_hint"] == "Output the sheet as valid JSON."
