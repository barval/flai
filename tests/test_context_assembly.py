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
    fact = {"content": "Пользователь предпочитает кофе без сахара", "score": 0.8, "metadata": {"kind": "preference"}}
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


@pytest.mark.unit
def test_only_scored_facts_and_the_profile_reach_the_prompt():
    """Measured on the live store: junk scored 0.63 for an unrelated query."""
    from modules.base import BaseModule

    facts = [
        {"content": "Сделай фото: тарелка с яблоками", "score": 0.31, "metadata": {"kind": "preference"}},
        {"content": "Любит кофе без сахара", "score": 0.72, "metadata": {"kind": "preference"}},
        {"content": "Работает системным администратором", "score": 0.05, "metadata": {"kind": "profile"}},
    ]

    selected = BaseModule.select_recalled_facts(facts, limit=7, min_score=0.5)

    texts = [f["content"] for f in selected]
    assert texts == ["Работает системным администратором", "Любит кофе без сахара"]


@pytest.mark.unit
def test_selection_keeps_the_limit_with_the_profile_first():
    from modules.base import BaseModule

    facts = [{"content": f"Факт {i}", "score": 0.9, "metadata": {"kind": "preference"}} for i in range(10)]
    facts.append({"content": "Профиль пользователя", "score": 0.0, "metadata": {"kind": "profile"}})
    facts.append({"content": "Живёт в Твери", "score": 0.0, "metadata": {"kind": "profile"}})

    selected = BaseModule.select_recalled_facts(facts, limit=3, min_score=0.5)

    assert len(selected) == 3
    assert {selected[0]["content"], selected[1]["content"]} == {"Профиль пользователя", "Живёт в Твери"}


@pytest.mark.unit
def test_context_does_not_inject_an_unscored_fact(base_module):
    fact = {"content": "Сделай фото: тарелка с яблоками", "score": 0.2, "metadata": {"kind": "preference"}}
    base_module.app.modules["slm"] = MagicMock()
    base_module.app.modules["slm"].recall.return_value = [fact]

    with patch(
        "modules.base.get_session_text_history",
        return_value=([], {"dropped_count": 0, "oldest_kept_id": None}),
    ):
        ctx = base_module._get_context_for_model("s1", "multimodal", "что я люблю?", user_id="u1")

    assert "яблоками" not in ctx


@pytest.mark.unit
def test_reasoning_messages_are_system_plus_user_with_the_question_last(base_module):
    """The prompt used to be one user turn: question first, evidence buried."""
    messages = base_module.build_reasoning_messages(
        "сколько стоит билет?",
        "2026-10-27 10:00 (Europe/Moscow)",
        "ru",
        "neutral",
        "Найденная информация из документов:\nЦена 500 рублей",
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "2026-10-27 10:00" in messages[0]["content"]
    assert "500 рублей" in messages[0]["content"] or "500 рублей" in messages[1]["content"]

    user = messages[1]["content"]
    assert user.index("500 рублей") < user.index("сколько стоит билет?")
    assert user.rstrip().endswith("сколько стоит билет?")


@pytest.mark.unit
def test_reasoning_messages_use_the_english_label(base_module):
    messages = base_module.build_reasoning_messages("how much is the ticket?", "2026-10-27", "en", "neutral", "")

    assert messages[1]["content"].rstrip().endswith("how much is the ticket?")
    assert "REQUEST" in messages[1]["content"]


@pytest.mark.unit
def test_session_summary_is_read_from_storage_not_generated_inline(base_module):
    """Regenerating it inside the request loaded the multimodal model mid-answer."""
    base_module.app.request_queue = MagicMock()
    stored = {"summary": "Кратко: выбрали ламинат", "summary_upto_id": 10}

    with (
        patch("modules.base.get_session_summary", return_value=stored),
        patch.object(base_module, "_summarize_session_history") as summarize,
    ):
        section = base_module._get_session_summary_section("s1", 40, "ru")

    assert "ламинат" in section
    summarize.assert_not_called()
    base_module.app.request_queue.add_session_summary_task.assert_called_once_with("s1", 40, "ru")


@pytest.mark.unit
def test_session_summary_is_not_rescheduled_when_it_already_covers_the_cut(base_module):
    base_module.app.request_queue = MagicMock()
    stored = {"summary": "Кратко: всё покрыто", "summary_upto_id": 40}

    with patch("modules.base.get_session_summary", return_value=stored):
        base_module._get_session_summary_section("s1", 40, "ru")

    base_module.app.request_queue.add_session_summary_task.assert_not_called()
