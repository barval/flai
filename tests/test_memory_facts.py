# tests/test_memory_facts.py
"""Memory extraction: what the model returns, and what is allowed to be stored."""

import pytest

from app.memory_facts import looks_personal, merge_into_profile, parse_memory_facts

pytestmark = pytest.mark.unit


def test_parses_a_clean_json_array():
    raw = '[{"fact": "Пользователь живёт в Нижнем Новгороде", "kind": "profile", "confidence": 0.9}]'

    facts = parse_memory_facts(raw)

    assert facts == [{"fact": "Пользователь живёт в Нижнем Новгороде", "kind": "profile", "confidence": 0.9}]


def test_parses_a_fenced_array_with_prose_around_it():
    raw = 'Вот результат:\n```json\n[{"fact": "Предпочитает кофе без сахара", "kind": "preference", "confidence": 0.8}]\n```\n'

    facts = parse_memory_facts(raw)

    assert [f["fact"] for f in facts] == ["Предпочитает кофе без сахара"]


def test_drops_a_question_echoed_as_a_fact():
    """The model tends to repeat the question; a question is not a fact."""
    raw = '[{"fact": "Что мы обсуждали про доставку?", "kind": "profile", "confidence": 0.9}]'

    assert parse_memory_facts(raw) == []


def test_drops_low_confidence_entries():
    raw = '[{"fact": "Возможно, любит рыбу", "kind": "preference", "confidence": 0.3}]'

    assert parse_memory_facts(raw) == []


def test_drops_entries_without_text_or_with_garbage_confidence():
    raw = '[{"fact": "", "kind": "profile", "confidence": 0.9}, {"fact": "Работает в Сбере", "confidence": "n/a"}]'

    assert parse_memory_facts(raw) == []


def test_unknown_kind_falls_back_to_preference():
    facts = parse_memory_facts('[{"fact": "Любит тёмную тему", "kind": "whatever", "confidence": 0.7}]')

    assert facts[0]["kind"] == "preference"


def test_deduplicates_and_caps_the_list():
    raw = (
        "["
        + ",".join(
            f'{{"fact": "Факт номер {i}", "kind": "profile", "confidence": 0.9}}' for i in range(8)
        )
        + ']'
    )

    facts = parse_memory_facts(raw)

    assert len(facts) == 5


def test_clamps_a_long_fact():
    raw = '[{"fact": "' + "очень длинная фраза " * 40 + '", "kind": "profile", "confidence": 0.9}]'

    facts = parse_memory_facts(raw)

    assert len(facts[0]["fact"]) <= 200
    assert facts[0]["fact"].endswith("...")


def test_garbage_returns_nothing():
    assert parse_memory_facts("Извините, я не могу это сделать.") == []
    assert parse_memory_facts("") == []


def test_looks_personal_accepts_a_first_person_statement():
    assert looks_personal("Я работаю программистом в Сбере") is True
    assert looks_personal("My timezone is Europe/Moscow") is True
    assert looks_personal("запомни, что я не ем грибы") is True


def test_looks_personal_rejects_a_task_or_a_short_message():
    assert looks_personal("Нарисуй кота в шляпе") is False
    assert looks_personal("привет") is False
    assert looks_personal("какая сегодня погода в Москве?") is False


def test_profile_block_keeps_only_profile_facts_and_previous_text():
    facts = [
        {"fact": "Работает системным администратором", "kind": "profile", "confidence": 0.9},
        {"fact": "Любит кофе без сахара", "kind": "preference", "confidence": 0.8},
    ]

    block = merge_into_profile(facts, previous="Живёт в Нижнем Новгороде")

    assert "Нижнем Новгороде" in block
    assert "системным администратором" in block
    assert "кофе" not in block


def test_profile_block_is_bounded():
    facts = [{"fact": f"Факт {i}", "kind": "profile", "confidence": 0.9} for i in range(30)]

    block = merge_into_profile(facts)

    assert len(block.splitlines()) <= 12


def test_extraction_template_renders_for_both_languages():
    """JSON braces in the template must be escaped or str.format consumes them."""
    from app.utils import format_prompt

    for lang in ("ru", "en"):
        out = format_prompt(
            "memory_extract.template",
            {"known_facts": "-", "user_message": "текст", "assistant_message": "ответ"},
            lang=lang,
        )
        assert out, lang
        assert '"fact"' in out, lang
        assert "{known_facts}" not in out, lang


def test_profile_template_renders_for_both_languages():
    from app.utils import format_prompt

    for lang in ("ru", "en"):
        out = format_prompt(
            "memory_profile.template",
            {"facts": "- живёт в Твери", "response_language": "Русский"},
            lang=lang,
        )
        assert out, lang
        assert "Твери" in out, lang


def test_language_follows_the_message_not_the_interface():
    from app.memory_facts import detect_language

    assert detect_language("Я увлекаюсь астрономией", fallback="en") == "ru"
    assert detect_language("I collect vinyl records", fallback="ru") == "en"
    assert detect_language("", fallback="en") == "en"
