# tests/test_tasks_continuity.py
"""Visual continuity sheet tests: pure helpers, the continuity direct-step
runner, the executor guard/replan/verifier wiring, the rebuild endpoint and
the queued-prompt hygiene (spec §3-§9a)."""

import json
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

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


# ── Executor: the deterministic guard + replan re-arm (app/tasks/executor.py) ──


import os as _os  # noqa: E402

import app.tasks.executor as ex  # noqa: E402  (module seam for monkeypatching)

# The executor/API fixtures are duplicated here (NOT imported) on purpose:
# pytest registers imported fixture functions too, and re-declaring a fixture
# at the test site then trips ruff's F811. Same bodies, one comment instead.

_db_available = (
    bool(_os.environ.get("DATABASE_URL")) and _os.environ["DATABASE_URL"] != "postgresql://localhost:5432/flai_test"
)


@pytest.fixture
def gate_open(monkeypatch):
    """Gate forced open at the executor seam (no Redis read)."""
    monkeypatch.setattr(ex, "is_gate_open", lambda *a, **k: True)


@pytest.fixture
def db_env(test_app, monkeypatch):
    """Store-backed executor environment; skip when no real DB is reachable."""
    if not _db_available:
        pytest.skip("No reachable PostgreSQL for tasks continuity tests")
    from app.database import get_db, init_db

    try:
        init_db()
        ex.get_task("00000000-0000-0000-0000-000000000000")
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")

    # No Redis flag is ever set here (the shared mock answers None).
    test_app.request_queue.redis.get.return_value = None
    # The step loop reviews the remaining plan after every consumed step; the
    # default stub answers «no change» so no test pays for a model call.
    monkeypatch.setattr(ex, "_run_replan", lambda *a, **k: '{"changed": false}')
    # Stop the create_app()-started executor singleton: manual drives only.
    ex.stop_task_executor(timeout=2)
    with ex._executor_lock:
        ex._EXECUTOR = None
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM task_steps")
        c.execute("DELETE FROM tasks")
    yield ex
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM task_steps")
        c.execute("DELETE FROM tasks")


@pytest.fixture
def tasks_env(test_app):
    """Store-backed environment for the tasks routes; skip without a real DB."""
    if not _db_available:
        pytest.skip("No reachable PostgreSQL for tasks continuity tests")
    from app.database import get_db, init_db

    try:
        init_db()
        ex.get_task("00000000-0000-0000-0000-000000000000")
    except Exception as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")

    test_app.request_queue.redis.get.return_value = None
    ex.stop_task_executor(timeout=2)
    with ex._executor_lock:
        ex._EXECUTOR = None
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM task_steps")
        c.execute("DELETE FROM tasks")
    yield ex
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM task_steps")
        c.execute("DELETE FROM tasks")


from tests.test_tasks_api import _login, _seed_task  # noqa: E402
from tests.test_tasks_executor import _cycle, _mk_task  # noqa: E402


def _image_plan(n=2, tail_llm=False):
    """A media-heavy plan as the planner would emit it (without a continuity step)."""
    steps = [{"seq": i + 1, "goal": f"Draw scene {i + 1}", "tool": "image_gen",
              "checkpoint_criteria": "image drawn"} for i in range(n)]
    if tail_llm:
        steps.append({"seq": n + 1, "goal": "Summarize", "tool": "llm", "checkpoint_criteria": "text"})
    return {"title": "Illustrate", "steps": steps}


def _fake_extraction(calls, sheet=None, fail=False):
    """Patch target for ``ex._continuity_extraction_call``: records every call."""

    def _call(app, row, digest, cancel_check, lang="ru", task_request="", plan_text=""):
        calls.append({"row": row, "plan_text": plan_text})
        if fail:
            raise RuntimeError("continuity sheet is not a valid JSON object")
        return sheet if sheet is not None else _sheet_json()

    return _call


@pytest.mark.unit
def test_guard_extracts_before_first_image_step(db_env, test_app, gate_open, monkeypatch):
    """Two image_gen steps + planner WITHOUT a continuity step: the guard runs
    the extraction once before the first one and stores the sheet."""
    task_id = _mk_task(db_env, status="active", step_plan=_image_plan(2))
    monkeypatch.setattr(ex.TaskExecutor, "_enqueue_subtask", lambda self, *a, **k: None)
    calls = []
    monkeypatch.setattr(ex, "_continuity_extraction_call", _fake_extraction(calls))

    _cycle(test_app)
    t = db_env.get_task(task_id)
    assert t["visual_bible"]["style"], "the guard populated the sheet before the first image step"
    assert len(calls) == 1, "extraction ran exactly once"

    _cycle(test_app)
    assert len(calls) == 1, "second media step must not re-extract an existing sheet"
    t2 = db_env.get_task(task_id)
    assert t2["status"] in ("active", "completed") and not t2["visual_bible_attempted"]


@pytest.mark.unit
def test_guard_gives_up_after_one_corrective_retry(db_env, test_app, gate_open, monkeypatch):
    """Simulated parse failure twice: ``visual_bible_attempted=True``, no more
    LLM calls afterwards, and the task continues."""
    task_id = _mk_task(db_env, status="active", step_plan=_image_plan(2, tail_llm=True))
    monkeypatch.setattr(ex.TaskExecutor, "_enqueue_subtask", lambda self, *a, **k: None)
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: {"result": "summary", "exec_seconds": 0.1,
                                                               "status": "passed", "checkpoint": None})
    calls = []
    monkeypatch.setattr(ex, "_continuity_extraction_call", _fake_extraction(calls, fail=True))

    _cycle(test_app)
    t = db_env.get_task(task_id)
    assert t["visual_bible_attempted"] is True, "give-up flag stamps the wasted retries"
    assert t["visual_bible"] == {} and len(calls) == 2, "exactly one inline corrective retry"

    _cycle(test_app)
    assert len(calls) == 2, "the give-up flag must stop any further extraction attempts"
    assert db_env.get_task(task_id)["status"] in ("active", "completed")


@pytest.mark.unit
def test_guard_skips_single_image_plan(db_env, test_app, gate_open, monkeypatch):
    task_id = _mk_task(db_env, status="active", step_plan=_image_plan(1))
    monkeypatch.setattr(ex.TaskExecutor, "_enqueue_subtask", lambda self, *a, **k: None)
    calls = []
    monkeypatch.setattr(ex, "_continuity_extraction_call", _fake_extraction(calls))

    _cycle(test_app)
    assert not calls and db_env.get_task(task_id)["visual_bible"] == {}, "one illustration needs no sheet"


@pytest.mark.unit
def test_guard_respects_disabled_flag(db_env, test_app, gate_open, monkeypatch):
    test_app.config["TASK_CONTINUITY_ENABLED"] = False
    task_id = _mk_task(db_env, status="active", step_plan=_image_plan(2))
    monkeypatch.setattr(ex.TaskExecutor, "_enqueue_subtask", lambda self, *a, **k: None)
    calls = []
    monkeypatch.setattr(ex, "_continuity_extraction_call", _fake_extraction(calls))

    _cycle(test_app)
    assert not calls and db_env.get_task(task_id)["visual_bible"] == {}


@pytest.mark.unit
def test_guard_skips_edits_only_plan(db_env, test_app, gate_open, monkeypatch):
    """Edits-only task: no sheet — an invented style must never alter the user's photos."""
    plan = {"title": "Edits", "steps": [
        {"seq": 1, "goal": "Edit photo 1", "tool": "image_edit", "checkpoint_criteria": "edited"},
        {"seq": 2, "goal": "Edit photo 2", "tool": "image_edit", "checkpoint_criteria": "edited"},
    ]}
    task_id = _mk_task(db_env, status="active", step_plan=plan)
    monkeypatch.setattr(ex.TaskExecutor, "_enqueue_subtask", lambda self, *a, **k: None)
    calls = []
    monkeypatch.setattr(ex, "_continuity_extraction_call", _fake_extraction(calls))

    _cycle(test_app)
    assert not calls and db_env.get_task(task_id)["visual_bible"] == {}


@pytest.mark.unit
def test_replan_rearms_and_next_media_step_merges(db_env, test_app, gate_open, monkeypatch):
    """A replan that leaves pending media steps marks the sheet stale; the next
    media step runs a MERGE extraction (existing sheet in step extra)."""
    existing = {"style": "watercolor", "characters": [{"id": "cat", "aliases": ["кот"], "look": "a black cat"}],
                "places": [], "props": []}
    plan = {"title": "T", "steps": [{"seq": 1, "goal": "Describe the cat", "tool": "llm",
                                     "checkpoint_criteria": "text"}]}
    task_id = _mk_task(db_env, status="active", step_plan=plan)
    db_env.update_task(task_id, visual_bible=existing, next_step_index=1)

    executor = ex.TaskExecutor(app=test_app)
    task = db_env.get_task(task_id)
    executor._apply_replan(task, 1, [{"seq": 2, "goal": "Draw the cat's hut", "tool": "image_gen",
                                      "checkpoint_criteria": "image"}])
    t = db_env.get_task(task_id)
    assert t["visual_bible_stale"] is True and t["visual_bible_attempted"] is False

    calls = []
    monkeypatch.setattr(ex.TaskExecutor, "_enqueue_subtask", lambda self, *a, **k: None)
    monkeypatch.setattr(ex, "_continuity_extraction_call", _fake_extraction(calls))
    _cycle(test_app)
    assert calls and calls[0]["row"].get("extra", {}).get("existing_sheet") == existing, \
        "merge extraction must receive the existing sheet verbatim"
    t2 = db_env.get_task(task_id)
    assert t2["visual_bible"]["props"], "the merged sheet came through"
    assert not t2["visual_bible_stale"] and not t2["visual_bible_attempted"]


@pytest.mark.unit
def test_planned_continuity_step_populates_and_disarms_guard(db_env, test_app, gate_open, monkeypatch):
    """A planner-scheduled continuity step writes the sheet on success; a
    failed one stamps the give-up flag (no silent retry loop)."""
    plan = {"title": "T", "steps": [
        {"seq": 1, "goal": "Extract the sheet", "tool": "continuity", "checkpoint_criteria": "json"},
        {"seq": 2, "goal": "Draw scene 1", "tool": "image_gen", "checkpoint_criteria": "image"},
        {"seq": 3, "goal": "Draw scene 2", "tool": "image_gen", "checkpoint_criteria": "image"},
    ]}
    task_id = _mk_task(db_env, status="active", step_plan=plan)
    monkeypatch.setattr(ex.TaskExecutor, "_enqueue_subtask", lambda self, *a, **k: None)
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: {"result": _sheet_json(), "exec_seconds": 0.1,
                                                               "status": "passed",
                                                               "checkpoint": {"verdict": True, "explanation": "ok",
                                                                              "exec_seconds": 0.1}})
    monkeypatch.setattr(ex, "_run_final_summary", lambda *a, **k: "final")
    calls = []
    monkeypatch.setattr(ex, "_continuity_extraction_call", _fake_extraction(calls))

    _cycle(test_app)
    t = db_env.get_task(task_id)
    assert t["visual_bible"]["style"], "the planner's own continuity step stores the sheet"
    assert not calls, "the guard must not re-extract when the planner's step succeeded"

    # Give-up path of the same visible step: a failed continuity step stamps attempted.
    monkeypatch.setattr(ex, "run_direct_step", lambda *a, **k: {"result": "continuity sheet is not a valid JSON object",
                                                               "exec_seconds": 0.1, "status": "error",
                                                               "checkpoint": None})
    task2 = _mk_task(db_env, status="active", step_plan=plan, minutes_ago=10)
    _cycle(test_app)
    t2 = db_env.get_task(task2)
    assert t2["visual_bible_attempted"] is True and t2["visual_bible"] == {}


# ── Over-budget policy (spec §8) ─────────────────────────────────────────


@pytest.mark.unit
def test_over_budget_compact_retry_then_accept_whole(db_env, test_app, gate_open, monkeypatch):
    """Rendered sheet over budget: one corrective re-extraction; a compacted
    sheet wins on the retry."""
    big = json.dumps({"style": "s" * 150, "characters": [], "places": [], "props": []}, ensure_ascii=False)
    small = json.dumps({"style": "compact style", "characters": [], "places": [], "props": []}, ensure_ascii=False)
    responses = iter([big, small])
    test_app.config["TASK_CONTINUITY_MAX_CHARS"] = 100
    task_id = _mk_task(db_env, status="active", step_plan=_image_plan(2))
    monkeypatch.setattr(ex.TaskExecutor, "_enqueue_subtask", lambda self, *a, **k: None)
    calls = []

    def _record(*a, **k):
        calls.append(1)
        return next(responses)

    monkeypatch.setattr(ex, "_continuity_extraction_call", _record)

    _cycle(test_app)
    t = ex.get_task(task_id)
    assert t["visual_bible"]["style"] == "compact style", "a corrective compact re-extraction ran once"
    assert len(calls) == 2


@pytest.mark.unit
def test_over_budget_after_retry_is_accepted_whole(db_env, test_app, gate_open, monkeypatch):
    """Still over budget after the corrective retry: accepted WHOLE, no flags."""
    big = json.dumps({"style": "s" * 150, "characters": [], "places": [], "props": []}, ensure_ascii=False)
    test_app.config["TASK_CONTINUITY_MAX_CHARS"] = 100
    task_id = _mk_task(db_env, status="active", step_plan=_image_plan(2))
    monkeypatch.setattr(ex.TaskExecutor, "_enqueue_subtask", lambda self, *a, **k: None)
    calls = []
    monkeypatch.setattr(ex, "_continuity_extraction_call",
                        _fake_extraction(calls, sheet=big))

    _cycle(test_app)
    t = db_env.get_task(task_id)
    assert t["visual_bible"]["style"] == "s" * 150, "the sheet is never truncated"
    assert len(calls) == 2 and not t["visual_bible_attempted"]


# ── Transient verifier clause (spec §5) ──────────────────────────────────


@pytest.mark.unit
def test_verifier_clause_appended_from_sheet(db_env, test_app, gate_open, monkeypatch):
    """A consumed subtask result with a sheet present: the checkpoint runner
    receives the continuity clause, the persisted criteria stay UNCHANGED."""
    bible = {"style": "watercolor", "characters": [], "places": [],
             "props": [{"id": "owl", "aliases": ["сова"], "look": "large brown owl"}]}
    plan = {"title": "T", "steps": [{"seq": 1, "goal": "Разговор сов на ветке", "tool": "image_gen",
                                     "checkpoint_criteria": "image matches the scene"}]}
    task_id = _mk_task(db_env, status="active", step_plan=plan)
    db_env.update_task(task_id, visual_bible=bible)

    from app.tasks.store import add_step, update_step

    row_id = add_step(task_id=task_id, seq=1, kind="subtask", goal="Разговор сов на ветке", tool="image_gen",
                      checkpoint_criteria="image matches the scene")
    update_step(row_id, status="subtask_running", extra={"subtask_queue_id": "q1"})

    captured = {}
    checkpoint = {"verdict": True, "explanation": "ok", "exec_seconds": 0.1}
    monkeypatch.setattr(ex, "run_media_checkpoint", lambda app, step, **k: (captured.setdefault("step", step),
                                                                          checkpoint)[1])
    queue = MagicMock()
    queue.check_result.return_value = {
        "status": "completed", "response": "the image is ready", "file_path": f"{task_id}/pic.png",
        "file_name": "pic.png", "file_type": "image/png", "file_size": 100,
    }
    monkeypatch.setattr(ex, "_get_request_queue", lambda: queue)
    monkeypatch.setattr(ex, "get_model_config", lambda *a, **k: {})
    monkeypatch.setattr(ex.TaskExecutor, "_describe_step_image", lambda self, media, lang, preview_data=None: ("desc", 0.0))

    executor = ex.TaskExecutor(app=test_app)
    row = db_env.get_step(row_id)
    out = executor._consume_subtask_result(task_id, 0, row, "q1", queue.check_result.return_value)
    assert out == "done"
    crit = captured["step"]["checkpoint_criteria"]
    assert "Continuity" in crit and "owl" in crit and "large brown owl" in crit
    assert "Style: watercolor" in crit
    stored = db_env.get_step(row_id)
    assert stored["checkpoint_criteria"] == "image matches the scene", "the clause is transient, never persisted"


# ── Rebuild endpoint (spec §7) ───────────────────────────────────────────


@pytest.mark.unit
def test_rebuild_endpoint_requires_login(client):
    response = client.post("/api/tasks/00000000-0000-0000-0000-000000000000/continuity")
    assert response.status_code == 401
    assert response.get_json()["error"].startswith("⚠️ ")


@pytest.mark.unit
def test_rebuild_endpoint_owner_scoped(tasks_env, client):
    task_id = _seed_task(user_id="owner", status="completed")
    _login(client, login="intruder")
    response = client.post(f"/api/tasks/{task_id}/continuity")
    assert response.status_code == 404


@pytest.mark.unit
def test_rebuild_endpoint_rejects_queued(tasks_env, client):
    task_id = _seed_task(user_id="owner", status="queued")
    _login(client)
    response = client.post(f"/api/tasks/{task_id}/continuity")
    assert response.status_code == 409
    assert response.get_json()["error"].startswith("⚠️ ")


@pytest.mark.unit
def test_rebuild_endpoint_rejects_double_run(tasks_env, client, monkeypatch, test_app):
    task_id = _seed_task(user_id="owner", status="completed")
    _login(client)
    test_app.request_queue.redis.set.return_value = False
    response = client.post(f"/api/tasks/{task_id}/continuity")
    assert response.status_code == 409


@pytest.mark.unit
def test_rebuild_endpoint_works_on_completed(tasks_env, client, monkeypatch, test_app):
    """The rebuild runs the extraction in a background thread, keeps the task
    status (completed) and stores the fresh sheet."""
    import app.routes.tasks as routes_tasks

    task_id = _seed_task(user_id="owner", status="completed")
    _login(client)
    test_app.request_queue.redis.set.return_value = True
    test_app.request_queue.redis.delete.return_value = 1
    calls = []

    class _FakeThread:
        def __init__(self, target=None, kwargs=None, daemon=True, name=None):
            self._target, self._kwargs = target, kwargs or {}
            self.daemon = daemon

        def start(self):
            self._target(**self._kwargs)  # synchronous — no race with the asserts

    class _FakeExecutor:
        def __init__(self, app=None, redis_client=None):
            self.app = app

        def _extract_visual_bible(self, task, merge=False):
            calls.append({"task": task["id"], "merge": merge})
            from app.tasks.store import update_task

            update_task(task["id"], visual_bible={"style": "fresh style", "characters": [], "places": [], "props": []})

    monkeypatch.setattr(ex, "TaskExecutor", _FakeExecutor)
    monkeypatch.setattr(routes_tasks.threading, "Thread", _FakeThread)

    response = client.post(f"/api/tasks/{task_id}/continuity")
    assert response.status_code == 202
    assert len(calls) == 1 and calls[0]["merge"] is False, "rebuild ran the extraction once, merge=False"
    assert tasks_env.get_task(task_id)["status"] == "completed", "no status transition"
    assert tasks_env.get_task(task_id)["visual_bible"]["style"] == "fresh style"


# ── Queue prompt hygiene (spec §9a) ──────────────────────────────────────


def _stubbed_queue(monkeypatch):
    """A RedisRequestQueue with every GPU-side call stubbed to no-ops."""
    from app.queue import RedisRequestQueue

    app = MagicMock()
    app.config = {"REDIS_URL": "redis://localhost:6379/0"}
    with patch("app.queue.redis.from_url", return_value=MagicMock()):
        q = RedisRequestQueue(app, start_workers=False)
    for name in ("_log_gpu_state_before_op", "_unload_llamacpp_models", "_unload_video_pipeline",
                 "_publish_stream_event"):
        monkeypatch.setattr(q, name, lambda *a, **k: None)
    monkeypatch.setattr(q, "_wait_for_vram", lambda *a, **k: True)
    monkeypatch.setattr(q, "_check_vram_ready", lambda *a, **k: True)
    monkeypatch.setattr(q, "_build_error_response", lambda sid, msg, t, lang, **k: {"error": msg})
    return q


@pytest.mark.unit
def test_image_gen_query_sanitized_before_prompt_builder(monkeypatch):
    q = _stubbed_queue(monkeypatch)
    captured = {}

    def fake_params(query, lang="ru", response_style="neutral"):
        captured["query"] = query
        return None, "stop-the-test"

    q.app.modules = {
        "image": MagicMock(available=True),
        "base": MagicMock(),
        "multimodal": MagicMock(generate_image_params=fake_params),
    }
    monkeypatch.setattr(q, "_is_task_cancelled", lambda task_id: False)
    q._process_image_gen_task(
        "Нарисуй красочную иллюстрацию к Сцене 8: Баба Яга — второе появление (строки ~540–621)",
        "sess", "user", "ru",
        task={"id": "t1", "user_id": "u", "session_id": "sess", "extra": {}, "data": {}},
    )
    prompt = captured["query"]
    assert "540" not in prompt and "(строки" not in prompt, "the source-reference marker must never reach the prompt"
    assert "Сцене 8" in prompt, "scene numbering is content, not a marker"
    assert "Баба Яга" in prompt


@pytest.mark.unit
def test_image_gen_query_keeps_quoted_inscription(monkeypatch):
    q = _stubbed_queue(monkeypatch)
    captured = {}

    def fake_params(query, lang="ru", response_style="neutral"):
        captured["query"] = query
        return None, "stop-the-test"

    q.app.modules = {
        "image": MagicMock(available=True),
        "base": MagicMock(),
        "multimodal": MagicMock(generate_image_params=fake_params),
    }
    monkeypatch.setattr(q, "_is_task_cancelled", lambda task_id: False)
    q._process_image_gen_task('Нарисуй вывеску "Открыто" к Сцене 8', "sess", "user", "ru",
                              task={"id": "t1", "user_id": "u", "session_id": "sess", "extra": {}, "data": {}})
    assert 'вывеску "Открыто"' in captured["query"]


@pytest.mark.unit
def test_image_edit_prompt_sanitized(monkeypatch):
    q = _stubbed_queue(monkeypatch)
    captured = {}

    def fake_edit(prompt, file_data, lang="ru"):
        captured["prompt"] = prompt
        return None, "stop-the-test"

    q.app.modules = {
        "base": MagicMock(),
        "multimodal": MagicMock(generate_edit_params=fake_edit),
    }
    monkeypatch.setattr(q, "_is_task_cancelled", lambda task_id: False)
    q._process_image_edit_task(
        "Убери мусор (lines 100-150), добавь букет", "", "", "sess", "user", "ru",
        task={"id": "t1", "user_id": "u", "session_id": "sess", "data": {}},
    )
    assert "(lines 100-150)" not in captured["prompt"] and "букет" in captured["prompt"]


# ── UI structure (spec §7) ───────────────────────────────────────────────


_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _translations_catalog(lang: str) -> dict:
    """Rough msgid -> msgstr parse of the Flask .po catalog (single-line entries)."""
    text = (_ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po").read_text(encoding="utf-8")
    catalog: dict[str, str] = {}
    entry_id = None
    for line in text.splitlines():
        raw = line.strip()
        if raw.startswith('msgid "'):
            entry_id = raw[7:-1] if raw.endswith('"') else raw[7:]
        elif raw.startswith('msgstr "') and entry_id:
            catalog[entry_id] = raw[8:-1] if raw.endswith('"') else raw[8:]
            entry_id = None
    return catalog


@pytest.mark.unit
@pytest.mark.parametrize("msgid", [
    "Extracting the visual continuity sheet...",
    "Could not build the visual continuity sheet — images continue without it.",
    "Visual continuity",
    "Rebuild the visual continuity sheet",
])
def test_continuity_ui_strings_localized(msgid):
    ru = _translations_catalog("ru")
    en = _translations_catalog("en")
    assert msgid in en and en[msgid], f"missing en translation for {msgid!r}"
    assert msgid in ru and ru[msgid], f"missing ru translation for {msgid!r}"


@pytest.mark.unit
def test_stage_labels_registered_in_events_js():
    events_js = (_ROOT / "app/static/js/events.js").read_text(encoding="utf-8")
    assert "continuity: 'stage_continuity'" in events_js
    chat_html = (_ROOT / "app/templates/chat.html").read_text(encoding="utf-8")
    assert "'stage_continuity': {{ _('Extracting the visual continuity sheet...')|tojson }}" in chat_html
    # The continuity sheet is an internal mechanism: no rebuild button, no
    # sheet block and no plan step are ever rendered in the task view.
    assert "rebuild-continuity-button" not in chat_html
    tasks_js = (_ROOT / "app/static/js/chat-tasks.js").read_text(encoding="utf-8")
    assert "renderContinuityBlock" not in tasks_js
    assert "rebuildCurrentTask" not in tasks_js
    chat_css = (_ROOT / "app/static/css/chat.css").read_text(encoding="utf-8")
    assert ".task-continuity" not in chat_css


@pytest.mark.unit
def test_ensure_style_forces_the_sheet_style_and_drops_photorealistic():
    """The prompt builder may drop the style; the sheet's style is mandatory.

    A non-photoreal style is put FIRST (a trailing style is ignored), gets the
    medium cues, and removes the realism tags that pull it toward photography.
    """
    from app.tasks.continuity import ensure_style_in_prompt

    bible = {"style": "children's book illustration, warm watercolor tones"}
    result = ensure_style_in_prompt("A cat on a rug, photorealistic, high detail, sharp focus.", bible)
    assert "photorealistic" not in result.lower()
    assert "high detail" not in result.lower() and "sharp focus" not in result.lower()
    assert result.startswith(
        "2D, hand-drawn, flat colors, clean outlines, children's book illustration, warm watercolor tones."
    )


@pytest.mark.unit
def test_ensure_style_keeps_photorealistic_for_a_photo_style():
    from app.tasks.continuity import ensure_style_in_prompt

    bible = {"style": "photorealistic, natural light"}
    result = ensure_style_in_prompt("A cat on a rug, photorealistic, high detail, sharp focus.", bible)
    assert result.startswith("photorealistic, natural light.")
    assert "photorealistic" in result.lower()
    assert "2D" not in result and "hand-drawn" not in result
    assert "high detail" in result and "sharp focus" in result


@pytest.mark.unit
def test_ensure_style_does_not_duplicate_an_exact_style():
    from app.tasks.continuity import ensure_style_in_prompt

    style = "children's book illustration"
    result = ensure_style_in_prompt("A cat, " + style, {"style": style})
    assert result.lower().count(style) == 1


@pytest.mark.unit
def test_ensure_style_does_not_force_2d_on_a_3d_style():
    """A 3D-cartoon series must not receive the 2D/hand-drawn illustration cues."""
    from app.tasks.continuity import ensure_style_in_prompt

    style = "3D cartoon render, soft plastic shapes"
    result = ensure_style_in_prompt("A cat, photorealistic, high detail, sharp focus, masterpiece.", {"style": style})
    assert result.startswith(style + ".")
    assert "2D" not in result
    assert "hand-drawn" not in result
    assert "photorealistic" not in result.lower()
    # 3D keeps the detail/sharpness quality tags; only photorealism is removed.
    assert "high detail" in result and "sharp focus" in result


@pytest.mark.unit
def test_style_lock_false_disables_style_enforcement():
    """A task that explicitly asks for different styles must keep them.

    The sheet reports style_lock=false with an empty style; the prompt builder
    and the continuity renderer must then leave the style alone.
    """
    from app.tasks.continuity import ensure_style_in_prompt, parse_bible

    sheet = parse_bible(
        '{"style": "", "style_lock": false, "characters": [], "places": [], "props": []}'
    )
    assert sheet is not None and sheet["style_lock"] is False
    prompt = "A cat, photorealistic, high detail, sharp focus"
    assert ensure_style_in_prompt(prompt, sheet) == prompt
    locked = render_sheet({"style": "children's book illustration", "style_lock": False})
    assert "Style:" not in locked


@pytest.mark.unit
def test_style_lock_missing_defaults_to_true():
    from app.tasks.continuity import parse_bible

    sheet = parse_bible('{"style": "watercolor", "characters": [], "places": [], "props": []}')
    assert sheet is not None and sheet["style_lock"] is True
