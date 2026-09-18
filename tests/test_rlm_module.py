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
        queue = RedisRequestQueue(app)
        queue._save_progress("t1", "task_progress", {"stage": "rlm_step", "step": 3})
    mapping = pipe.hset.call_args.kwargs["mapping"]
    assert mapping["stage"] == "rlm_step"
    assert mapping["count"] == "3"
