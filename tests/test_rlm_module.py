import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modules.rlm import RLM_TOOL_DEFINITIONS, RlmModule, RlmResult, _RlmBroker


@pytest.mark.unit
def test_rlm_config_defaults(app):
    assert app.config["RLM_ENABLED"] in (True, False)
    assert app.config["RLM_MAX_STEPS"] >= 1
    assert app.config["RLM_CODE_TIMEOUT"] >= 1
    assert app.config["RLM_OBS_TRUNC"] >= 100
    assert app.config["RLM_SUB_MAX_TOKENS"] >= 16
    assert app.config["RLM_WEB_MAX_FETCHES"] >= 0


@pytest.mark.unit
def test_rlm_tool_definitions_shape():
    names = {t["function"]["name"] for t in RLM_TOOL_DEFINITIONS}
    assert names == {"python", "llm", "web_fetch", "final"}
    for tool in RLM_TOOL_DEFINITIONS:
        assert tool["type"] == "function"
        assert "parameters" in tool["function"]


@pytest.mark.unit
def test_build_system_prompt_renders_with_literal_braces(test_app):
    with test_app.app_context():
        module = RlmModule.__new__(RlmModule)
        prompt = module.build_system_prompt("en")
        assert prompt, "template must render non-empty"
        assert str(test_app.config["RLM_MAX_STEPS"]) in prompt
        assert "{filename: text}" in prompt
        ru_prompt = module.build_system_prompt("ru")
        assert ru_prompt, "ru template must render non-empty"
        assert "{имя_файла: текст}" in ru_prompt


@pytest.mark.unit
def test_rlm_build_user_prompt_lists_corpus():
    module = RlmModule.__new__(RlmModule)
    prompt = module.build_user_prompt("What is X?", {"a.txt": "hello", "b.md": "world!"})
    assert "a.txt" in prompt and "b.md" in prompt
    assert "What is X?" in prompt


# --- Task 10: resource-adaptive step budget ---


def _patch_reasoning_context(monkeypatch, context_length):
    monkeypatch.setattr(
        "app.model_config.get_model_config",
        lambda module: {"context_length": context_length} if module == "reasoning" else None,
    )


@pytest.mark.unit
def test_effective_max_steps_limits_budget_to_context(test_app, monkeypatch):
    # A mid-size reasoning window cannot hold 18 full-size observations:
    # steps stay at the ceiling but the observation budget shrinks to fit.
    _patch_reasoning_context(monkeypatch, 16384)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: None)
    module = RlmModule.__new__(RlmModule)
    with test_app.app_context():
        steps = module._effective_max_steps(["system"], "en")
    assert steps == test_app.config["RLM_MAX_STEPS"]


@pytest.mark.unit
def test_effective_max_steps_unknown_context_keeps_ceiling(test_app, monkeypatch):
    # No known window AND no hardware info: trust the configured cap,
    # never lose analysis capability.
    monkeypatch.setattr("app.model_config.get_model_config", lambda module: None)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: None)
    module = RlmModule.__new__(RlmModule)
    with test_app.app_context():
        steps = module._effective_max_steps(["system"], "en")
    assert steps == test_app.config["RLM_MAX_STEPS"]


@pytest.mark.unit
def test_effective_max_steps_small_context_fits_at_least_one_step(test_app, monkeypatch):
    # Even a window too small for full steps must leave one step,
    # so the user gets a bounded attempt instead of a hard configuration error.
    _patch_reasoning_context(monkeypatch, 2048)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: None)
    module = RlmModule.__new__(RlmModule)
    with test_app.app_context():
        steps = module._effective_max_steps(["system"], "en")
    assert steps == 1


@pytest.mark.unit
def test_effective_max_steps_respects_config_ceiling(test_app, monkeypatch):
    _patch_reasoning_context(monkeypatch, 32768)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: None)
    test_app.config["RLM_MAX_STEPS"] = 2
    module = RlmModule.__new__(RlmModule)
    with test_app.app_context():
        steps = module._effective_max_steps(["system"], "en")
    assert steps == 2


# --- v12.1: resource-based step ladder ---


def _hw(vram_mb: int = 0, cpu: int = 8, ram: int = 32000, platform: str = "nvidia"):
    hw = MagicMock()
    hw.total_vram_mb = vram_mb
    hw.cpu_count = cpu
    hw.total_ram_mb = ram
    hw.platform = platform
    return hw


@pytest.mark.unit
def test_resource_step_budget_ladder(test_app):
    with test_app.app_context():
        from modules.rlm import _resource_step_budget

        assert _resource_step_budget(_hw(vram_mb=24576)) == 18  # 24 GB tier
        assert _resource_step_budget(_hw(vram_mb=16311)) == 12  # this host (16 GB usable)
        assert _resource_step_budget(_hw(vram_mb=12000)) == 10  # 12 GB tier
        assert _resource_step_budget(_hw(vram_mb=7900)) == 8  # 8 GB tier
        assert _resource_step_budget(_hw(platform="cpu")) == 6  # CPU-only
        assert _resource_step_budget(_hw(vram_mb=4096)) == 6  # <8 GB


@pytest.mark.unit
def test_resource_step_budget_unknown_hardware_means_no_extra_cap(test_app):
    from modules.rlm import _resource_step_budget

    assert _resource_step_budget(None) == 0


@pytest.mark.unit
def test_effective_max_steps_uses_resource_ladder(test_app, monkeypatch):
    # 12 GB GPU: ladder allows 10, context is huge -> 10.
    _patch_reasoning_context(monkeypatch, 32768)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: _hw(vram_mb=12000))
    with test_app.app_context():
        test_app.config["RLM_MAX_STEPS"] = 12
        module = RlmModule.__new__(RlmModule)
        steps = module._effective_max_steps(["system"], "en")
    assert steps == 10


@pytest.mark.unit
def test_obs_trunc_shrinks_to_fit_context_but_not_below_minimum(test_app, monkeypatch):
    """Steps must NOT be cut to fit observations into the window: the
    observation truncation shrinks instead, down to a usable minimum."""
    from modules.rlm import _obs_trunc_for_context

    # 24576 window (this host): usable observations for the full 12-step run.
    _patch_reasoning_context(monkeypatch, 24576)
    with test_app.app_context():
        assert 2000 <= _obs_trunc_for_context(12, 4000, boot_tokens=2000) < 4000
    # Tighter window: observations shrink further.
    _patch_reasoning_context(monkeypatch, 16384)
    with test_app.app_context():
        trunc = _obs_trunc_for_context(12, 4000, boot_tokens=2000)
        assert 800 <= trunc < 2788
    # Tiny window: never below the minimum usable observation.
    _patch_reasoning_context(monkeypatch, 4096)
    with test_app.app_context():
        assert _obs_trunc_for_context(12, 4000, boot_tokens=2000) == 800


@pytest.mark.unit
def test_run_uses_context_fitted_obs_trunc(test_app, monkeypatch):
    """run() must pass the fitted truncation into the loop, not the raw config."""
    _patch_reasoning_context(monkeypatch, 4096)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: _hw(vram_mb=16311))
    seen = {}

    import modules.rlm as rlm_mod

    original = rlm_mod.RlmModule._effective_max_steps

    def spy_effective(self, boot_texts, lang):
        steps = original(self, boot_texts, lang)
        seen["steps"] = steps
        return steps

    monkeypatch.setattr(rlm_mod.RlmModule, "_effective_max_steps", spy_effective)
    script = [
        {
            "content": "",
            "tool_calls": [{"id": "1", "function": {"name": "final", "arguments": json.dumps({"answer": "done"})}}],
        }
    ]
    module, _ = _make_module(script)
    with test_app.app_context():
        module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "x" * 10000},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert seen["steps"] >= 1


@pytest.mark.unit
def test_effective_max_steps_ladder_never_below_six(test_app, monkeypatch):
    # CPU host: ladder floor is 6 even when the context would allow fewer.
    _patch_reasoning_context(monkeypatch, 16384)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: _hw(platform="cpu", cpu=16))
    with test_app.app_context():
        test_app.config["RLM_MAX_STEPS"] = 12
        module = RlmModule.__new__(RlmModule)
        steps = module._effective_max_steps(["system"], "en")
    assert steps >= 6


@pytest.mark.unit
def test_effective_max_steps_ceiling_18_not_exceeded(test_app, monkeypatch):
    # Big GPU (48 GB) + huge context: ladder tops out at 18 even if the
    # context window could fit more.
    _patch_reasoning_context(monkeypatch, 131072)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: _hw(vram_mb=49152))
    with test_app.app_context():
        test_app.config["RLM_MAX_STEPS"] = 18
        module = RlmModule.__new__(RlmModule)
        steps = module._effective_max_steps(["system"], "en")
    assert steps == 18


# --- v12.1: step-derived task timeout ---


@pytest.mark.unit
def test_auto_task_timeout_gpu(test_app, monkeypatch):
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: _hw(vram_mb=16311))
    from modules.rlm import RlmModule

    with test_app.app_context():
        module = RlmModule.__new__(RlmModule)
        timeout = module._auto_task_timeout(steps=12)
    assert timeout == 120 + 12 * 90  # 1200s


@pytest.mark.unit
def test_auto_task_timeout_cpu_is_slower(test_app, monkeypatch):
    from modules.rlm import RlmModule

    monkeypatch.setattr("modules.rlm._get_hardware", lambda: _hw(vram_mb=16311))
    with test_app.app_context():
        module = RlmModule.__new__(RlmModule)
        gpu_t = module._auto_task_timeout(steps=6)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: _hw(platform="cpu", cpu=16))
    with test_app.app_context():
        cpu_t = module._auto_task_timeout(steps=6)
    assert cpu_t == 240 + 6 * 300  # 2040s
    assert cpu_t > gpu_t


@pytest.mark.unit
def test_task_timeout_zero_triggers_auto_derivation(test_app, monkeypatch):
    # RLM_TASK_TIMEOUT=0 now means "auto": derive from step budget + platform.
    _patch_reasoning_context(monkeypatch, 32768)
    monkeypatch.setattr("modules.rlm._get_hardware", lambda: _hw(vram_mb=16311))
    script = [
        {
            "content": "",
            "tool_calls": [{"id": "1", "function": {"name": "final", "arguments": json.dumps({"answer": "done"})}}],
        }
    ]
    module, llamacpp = _make_module(script)
    with test_app.app_context():
        test_app.config["RLM_TASK_TIMEOUT"] = 0
        test_app.config["RLM_MAX_STEPS"] = 12
        result = module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "x"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert result.answer == "done"
    assert result.error == ""


@pytest.mark.unit
def test_task_timeout_minus_one_disables_deadline(test_app):
    test_app.config["RLM_TASK_TIMEOUT"] = -1
    module, _ = _make_module(
        [
            {
                "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "final", "arguments": json.dumps({"answer": "done"})}}],
            },
        ]
    )
    with test_app.app_context():
        result = module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "abc"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert result.answer == "done"
    assert result.error == ""


@pytest.mark.unit
def test_build_system_prompt_uses_effective_max_steps(test_app):
    with test_app.app_context():
        module = RlmModule.__new__(RlmModule)
        prompt = module.build_system_prompt("en", max_steps=3)
        assert "At most 3 steps" in prompt


# --- Task 5: broker and actor loop ---


class ScriptedLlamacpp:
    """Returns scripted chat() responses in order."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, model_type="multimodal", lang="ru", validate=True, tools=None, temperature=None):
        self.calls.append({"messages": messages, "tools": tools})
        return self.script.pop(0)


def _make_module(script):
    module = RlmModule.__new__(RlmModule)
    module.logger = MagicMock()
    llamacpp = ScriptedLlamacpp(script)
    app = MagicMock()
    app.modules = {"base": MagicMock(llamacpp=llamacpp), "search": MagicMock()}
    module.app = app
    return module, llamacpp


@pytest.mark.unit
def test_run_python_then_final(test_app):
    script = [
        {
            "content": "",
            "tool_calls": [
                {"id": "1", "function": {"name": "python", "arguments": json.dumps({"code": "n = len(context['d'])"})}}
            ],
        },
        {
            "content": "",
            "tool_calls": [{"id": "2", "function": {"name": "final", "arguments": json.dumps({"answer": "7"})}}],
        },
    ]
    module, _ = _make_module(script)
    stages = []
    with test_app.app_context():
        result = module.run(
            task={"id": "t1"},
            question="How long?",
            corpus={"d": "abcdefg"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: stages.append(stage),
            is_cancelled=lambda: False,
        )
    assert isinstance(result, RlmResult)
    assert result.answer == "7"
    assert [t.tool for t in result.trace] == ["python"]
    assert "rlm_step" in stages


@pytest.mark.unit
def test_run_plain_string_response_is_answer(test_app):
    # chat() returns a plain string when the model answers without tool calls;
    # that string is the final answer, not an error.
    module, _ = _make_module(["Companies: Acme, Globex."])
    with test_app.app_context():
        result = module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "x"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert result.answer == "Companies: Acme, Globex."
    assert result.error == ""


@pytest.mark.unit
def test_run_error_string_response_is_error(test_app):
    module, _ = _make_module(["⚠️ Service temporarily unavailable"])
    with test_app.app_context():
        result = module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "x"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert result.answer == ""
    assert result.error == "⚠️ Service temporarily unavailable"


@pytest.mark.unit
def test_run_llm_tool_calls_broker(test_app):
    script = [
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "function": {"name": "llm", "arguments": json.dumps({"prompt": "summarize", "text": "abc"})},
                }
            ],
        },
        {
            "content": "",
            "tool_calls": [{"id": "2", "function": {"name": "final", "arguments": json.dumps({"answer": "ok"})}}],
        },
    ]
    module, llamacpp = _make_module(script)
    module.broker_llm = lambda prompt, text="": "sub-answer"  # patched seam
    with test_app.app_context():
        module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "abc"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert any("sub-answer" in str(c["messages"]) for c in llamacpp.calls)


@pytest.mark.unit
def test_run_cancelled_returns_error(test_app):
    module, _ = _make_module([])
    with test_app.app_context():
        result = module.run(
            task={"id": "t1"},
            question="q",
            corpus={},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: True,
        )
    assert result.error == "cancelled"


@pytest.mark.unit
def test_run_task_timeout_aborts_with_error(test_app, monkeypatch):
    # The configured RLM_TASK_TIMEOUT (2s) expires between the steps; the run
    # must abort with the "task timeout" sentinel instead of running forever.
    times = iter([1000.0, 1000.0, 1003.0])
    monkeypatch.setattr("modules.rlm.time.monotonic", lambda: next(times, 1003.0))
    test_app.config["RLM_TASK_TIMEOUT"] = 2
    module, _ = _make_module(
        [
            {"content": "", "tool_calls": [{"id": "1", "function": {"name": "llm", "arguments": "{}"}}]},
            {"content": "", "tool_calls": [{"id": "2", "function": {"name": "final", "arguments": "{}"}}]},
        ]
    )
    module.broker_llm = lambda prompt, text="": "sub-answer"
    with test_app.app_context():
        result = module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "abc"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert result.error == "task timeout"
    assert result.answer == ""


@pytest.mark.unit
def test_run_task_timeout_disabled_when_zero(test_app):
    # 0 disables the wall-clock deadline — the step limit remains the only bound.
    test_app.config["RLM_TASK_TIMEOUT"] = 0
    module, _ = _make_module(
        [
            {
                "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "final", "arguments": json.dumps({"answer": "done"})}}],
            }
        ]
    )
    with test_app.app_context():
        result = module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "abc"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert result.answer == "done"
    assert result.error == ""


@pytest.mark.unit
def test_web_fetch_dedup_repeated_queries(test_app):
    module, _ = _make_module([])
    queries = []
    search = MagicMock()
    search.available = True
    search.search.side_effect = lambda query, lang="ru", max_results=3: (
        queries.append(query) or [{"title": "t", "url": "u", "content": "page"}]
    )
    module.app.modules["search"] = search
    broker = _RlmBroker(module, "en", 1024, 5)
    first = module.broker_web_fetch("repeat me", broker)
    second = module.broker_web_fetch("repeat me", broker)
    assert queries == ["repeat me"]
    assert first.startswith("[")
    assert "already searched" in second


@pytest.mark.unit
def test_run_nudges_final_near_step_limit(test_app):
    def web_tool(i):
        return {
            "content": "",
            "tool_calls": [
                {"id": str(i), "function": {"name": "web_fetch", "arguments": json.dumps({"query": f"q{i}"})}}
            ],
        }

    script = [
        web_tool(1),
        web_tool(2),
        web_tool(3),
        {
            "content": "",
            "tool_calls": [{"id": "9", "function": {"name": "final", "arguments": json.dumps({"answer": "done"})}}],
        },
    ]
    module, llamacpp = _make_module(script)
    search = MagicMock()
    search.available = True
    search.search.return_value = [{"title": "t", "url": "u", "content": "page"}]
    module.app.modules["search"] = search
    with test_app.app_context():
        test_app.config["RLM_MAX_STEPS"] = 4
        result = module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "x"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert result.answer == "done"
    assert search.search.call_count == 3
    nudged = any(
        any(
            isinstance(m.get("content"), str) and "remain" in m["content"] and "final(answer)" in m["content"]
            for m in call["messages"]
        )
        for call in llamacpp.calls
    )
    assert nudged


@pytest.mark.unit
def test_run_last_step_has_no_tools_and_plain_text_becomes_answer(test_app):
    """On the final step the model must not be able to burn the step on
    another tool call: tools are withheld and a plain text response is the
    final answer (instead of 'step limit reached')."""
    script = [
        {"content": "", "tool_calls": [{"id": "1", "function": {"name": "python", "arguments": "{}"}}]},
        {"content": "The rubai numbering is 1378-1390, the central symbol is wine."},
    ]
    module, llamacpp = _make_module(script)
    with test_app.app_context():
        test_app.config["RLM_MAX_STEPS"] = 2
        result = module.run(
            task={"id": "t1"},
            question="q",
            corpus={"d": "x"},
            user_id="u",
            session_id="s",
            lang="en",
            on_stage=lambda stage, extra=None: None,
            is_cancelled=lambda: False,
        )
    assert result.answer == "The rubai numbering is 1378-1390, the central symbol is wine."
    assert result.error == ""
    assert llamacpp.calls[-1]["tools"] is None


@pytest.mark.unit
def test_rlm_stage_labels_present_in_events_js():
    js = Path("app/static/js/events.js").read_text(encoding="utf-8")
    for key in ("rlm_reading", "rlm_step", "rlm_searching_web", "rlm_submodel", "rlm_finalizing"):
        assert key in js


# --- Task 9: UI toggle and trace rendering ---


@pytest.mark.unit
def test_chat_html_has_deep_analysis_toggle():
    html = Path("app/templates/chat.html").read_text(encoding="utf-8")
    assert 'id="rlm-toggle"' in html
    assert "/api/rlm/analyze" in Path("app/static/js/chat-init.js").read_text(encoding="utf-8")


@pytest.mark.unit
def test_save_progress_stores_count_for_rlm_step():
    from unittest.mock import Mock, patch

    from app.queue import RedisRequestQueue

    app = Mock()
    app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
    app.logger = Mock()
    redis = Mock()
    pipe = Mock()
    redis.pipeline.return_value = pipe
    with patch("app.queue.redis.from_url", return_value=redis):
        queue = RedisRequestQueue(app, start_workers=False)
        queue._save_progress("t1", "task_progress", {"stage": "rlm_step", "step": 3})
    mapping = pipe.hset.call_args.kwargs["mapping"]
    assert mapping["stage"] == "rlm_step"
    assert mapping["count"] == "3"
