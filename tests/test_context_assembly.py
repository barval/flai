# tests/test_context_assembly.py
"""How the prompt is assembled: ordering, time anchor, token budget."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def base_module():
    from modules.base import BaseModule

    app = MagicMock()
    app.config = {"SLM_RECALL_LIMIT": 3}
    app.logger = MagicMock()
    app.modules = {}
    module = BaseModule(app)
    module._get_model_config = lambda model_type="multimodal": {"context_length": 10000}
    module._estimate_tokens = lambda text, model_type="multimodal", lang="ru": max(1, len(text or "") // 4)
    return module


@pytest.mark.unit
def test_reasoning_prompt_carries_the_current_time():
    """The reasoning model had no date at all: the template never used the value.

    `build_context_prompt()` drops message timestamps on the promise that the
    system prompt carries the time, and only the router template honoured it.
    """
    from app.utils import format_prompt

    prompt = format_prompt(
        "reasoning.template",
        {
            "current_time_str": "2026-10-26 20:15 (Europe/Moscow)",
            "reasoning_query": "что случилось на этой неделе?",
            "response_language": "Русский",
            "conversation_history": "",
            "response_style": "",
        },
        lang="ru",
    )

    assert "2026-10-26 20:15" in prompt


@pytest.mark.unit
def test_context_places_history_before_long_term_facts(base_module):
    """Facts change with every query, history only grows.

    Putting the facts ahead of the transcript broke the llama.cpp prefix cache
    on every turn: the whole history after them had to be re-processed.
    """
    fact = {"content": "Пользователь предпочитает кофе без сахара", "metadata": {"fact_type": "general"}}
    base_module.app.modules["slm"] = MagicMock()
    base_module.app.modules["slm"].recall.return_value = [fact]

    history = [{"role": "user", "content": "обсуждали кофе"}, {"role": "assistant", "content": "да, помню"}]
    with patch(
        "modules.base.get_session_text_history",
        return_value=(history, {"dropped_count": 0, "oldest_kept_id": 1}),
    ):
        ctx = base_module._get_context_for_model("s1", "multimodal", "что я люблю пить?", user_id="u1")

    assert ctx.index("обсуждали кофе") < ctx.index("Дополнительная информация")


@pytest.mark.unit
def test_context_places_summary_before_history(base_module):
    """The summary describes the trimmed prefix, so it must precede the rest."""
    history = [{"role": "user", "content": "продолжаем про ремонт"}]
    with (
        patch(
            "modules.base.get_session_text_history",
            return_value=(history, {"dropped_count": 8, "oldest_kept_id": 40}),
        ),
        patch.object(base_module, "_get_session_summary_section", return_value="\nКратко: выбирали ламинат"),
    ):
        ctx = base_module._get_context_for_model("s1", "multimodal", "что выбрали?", user_id="u1")

    assert ctx.index("Кратко: выбирали ламинат") < ctx.index("продолжаем про ремонт")


@pytest.mark.unit
def test_chunk_entry_omits_the_relevance_score():
    """A cosine value in the prompt is noise the model cannot interpret."""
    from app.utils import format_chunk_entry

    entry = format_chunk_entry("Источник", "resume.pdf", "Работал в Сбере")

    assert entry == "[Источник: resume.pdf]\nРаботал в Сбере"


@pytest.mark.unit
def test_reasoning_budget_leaves_room_for_the_answer():
    """ctx*0.4 (9830 on 24k) cannot be spent when the prompt takes 64% already."""
    from app.utils import CONTEXT_FILL_RATIO, REASONING_ANSWER_RESERVE_TOKENS, reasoning_budget_tokens

    for ctx in (24576, 32768, 16384, 8192):
        budget = reasoning_budget_tokens(ctx)
        prompt = int(ctx * CONTEXT_FILL_RATIO)
        assert budget + prompt + REASONING_ANSWER_RESERVE_TOKENS <= ctx


@pytest.mark.unit
def test_reasoning_budget_keeps_a_floor_on_tiny_windows():
    """A 4k window cannot satisfy the reserve; the floor still allows thinking."""
    from app.utils import reasoning_budget_tokens

    assert reasoning_budget_tokens(4096) == 1024


@pytest.mark.unit
def test_trimmed_history_does_not_start_with_an_orphan_answer():
    """The trim is a front cut, so it can leave an answer whose question went away."""
    from app.db import get_session_text_history
    from app.utils import estimate_tokens

    messages = [
        {"id": 1, "role": "user", "content": "вопрос про ламинат " * 4},
        {"id": 2, "role": "assistant", "content": "ответ про ламинат " * 4},
        {"id": 3, "role": "user", "content": "а какой цвет?"},
        {"id": 4, "role": "assistant", "content": "светлый дуб"},
    ]
    budget = sum(estimate_tokens(m["content"]) for m in messages[-3:])

    with patch("app.db.get_session_messages", return_value=messages):
        kept, meta = get_session_text_history("s1", budget, return_meta=True)

    assert [m["role"] for m in kept] == ["user", "assistant"]
    assert meta["dropped_count"] == 2
