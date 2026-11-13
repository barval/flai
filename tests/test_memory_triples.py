"""Typed personal facts: parsing, supersession and reopening.

The point of a triple is that "I moved to Tbilisi" can close "I live in Moscow"
without asking a model whether the two contradict each other.
"""

from datetime import UTC, datetime

import pytest

LOGIN = "triples_alice"


@pytest.fixture
def database(test_app):
    """An application context with this module's rows removed."""
    from app.database import get_db

    with test_app.app_context():
        with get_db() as conn:
            conn.cursor().execute("DELETE FROM memory_facts WHERE user_id = %s", (LOGIN,))
        with get_db() as conn:
            yield conn


def _triple(predicate: str = "lives_in", value: str = "Тбилиси", date: str = "2026-03-01") -> dict:
    from app.memory_triples import parse_triples

    answer = (
        f'[{{"predicate": "{predicate}", "value": "{value}", "sentence": "{value}", '
        f'"valid_from": "{date}", "confidence": 0.9}}]'
    )
    return parse_triples(answer, source=date)[0]


class TestParsing:
    def test_a_known_predicate_is_accepted(self):
        from app.memory_triples import parse_triples

        facts = parse_triples('[{"predicate": "lives_in", "value": "Тбилиси", "confidence": 0.9}]')

        assert len(facts) == 1
        assert facts[0]["predicate"] == "lives_in"
        assert facts[0]["value_norm"] == "тбилиси"

    def test_an_unknown_predicate_is_dropped(self):
        from app.memory_triples import parse_triples

        facts = parse_triples('[{"predicate": "lives_somewhere", "value": "Тбилиси", "confidence": 0.9}]')

        # A predicate outside the closed list could never be superseded, and a
        # fact that never contradicts anything is a fact that duplicates.
        assert facts == []

    def test_a_low_confidence_guess_is_dropped(self):
        from app.memory_triples import parse_triples

        assert parse_triples('[{"predicate": "lives_in", "value": "Тбилиси", "confidence": 0.3}]') == []

    def test_a_fenced_answer_with_prose_is_still_parsed(self):
        from app.memory_triples import parse_triples

        answer = 'Вот факты:\n```json\n[{"predicate": "works_at", "value": "стартап", "confidence": 0.8}]\n```'

        assert parse_triples(answer)[0]["predicate"] == "works_at"

    def test_case_and_trailing_punctuation_do_not_create_a_second_value(self):
        from app.memory_triples import normalize_value

        assert normalize_value("  Тбилиси. ") == normalize_value("тбилиси")


class TestIntervals:
    def test_a_newer_value_closes_the_previous_one(self, database):
        from app.memory_triples import active_triples, closed_triples, store_triple

        assert store_triple(database, LOGIN, _triple(value="Москва", date="2025-06-01")) == "added"
        assert store_triple(database, LOGIN, _triple(value="Тбилиси", date="2026-03-01")) == "superseded"

        assert [row["value"] for row in active_triples(database, LOGIN)] == ["Тбилиси"]
        closed = closed_triples(database, LOGIN)
        assert [row["value"] for row in closed] == ["Москва"]
        # The old value stopped holding when the new one started, not when we
        # happened to learn about it.
        assert closed[0]["valid_until"] == datetime(2026, 3, 1, tzinfo=UTC)

    def test_the_same_value_twice_changes_nothing(self, database):
        from app.memory_triples import active_triples, store_triple

        assert store_triple(database, LOGIN, _triple(value="Тбилиси")) == "added"
        assert store_triple(database, LOGIN, _triple(value="тбилиси.")) == "unchanged"

        assert len(active_triples(database, LOGIN)) == 1

    def test_moving_back_reopens_the_earlier_value(self, database):
        from app.memory_triples import active_triples, store_triple

        store_triple(database, LOGIN, _triple(value="Москва", date="2025-06-01"))
        store_triple(database, LOGIN, _triple(value="Тбилиси", date="2026-03-01"))
        assert store_triple(database, LOGIN, _triple(value="Москва", date="2026-09-01")) == "superseded"

        assert [row["value"] for row in active_triples(database, LOGIN)] == ["Москва"]


class TestAnswer:
    def test_triples_and_notes_are_separated(self):
        from app.memory_triples import parse_answer

        facts, notes = parse_answer(
            '{"facts": [{"predicate": "lives_in", "value": "Тбилиси", "confidence": 0.9}], '
            '"notes": ["двое детей"]}'
        )

        assert facts[0]["predicate"] == "lives_in"
        # A closed predicate list cannot hold everything a person says about
        # themselves; the notes keep the rest instead of dropping it.
        assert notes == ["двое детей"]

    def test_an_answer_without_json_is_empty(self):
        from app.memory_triples import parse_answer

        assert parse_answer("no facts here") == ([], [])


class TestRecallBlock:
    def test_active_facts_become_a_context_block(self, test_app, database):
        from app.memory_triples import store_triple

        store_triple(database, LOGIN, _triple(value="Тбилиси"))
        database.commit()
        base = test_app.modules["base"]

        with test_app.app_context():
            block = base._active_facts_block(LOGIN, "ru")

        assert "Тбилиси" in block

    def test_without_a_user_there_is_no_block(self, test_app):
        base = test_app.modules["base"]

        with test_app.app_context():
            assert base._active_facts_block(None) == ""


class TestDateGrounding:
    """A date is trusted only when the message states it.

    Measured with the schema example in the prompt: the model copied the
    example's date onto a message with no time at all, and once the sample lost
    its date it ignored real ones. The year is the anchor.
    """

    def test_a_year_in_the_message_keeps_the_date(self):
        from app.memory_triples import parse_triples

        facts = parse_triples(
            '[{"predicate": "lives_in", "value": "Тбилиси", "valid_from": "2026-03", "confidence": 0.9}]',
            source="В марте 2026 я переехал в Тбилиси",
        )

        assert facts[0]["valid_from"].year == 2026
        assert facts[0]["valid_from"].month == 3

    def test_a_date_the_message_does_not_mention_falls_back_to_now(self):
        from datetime import UTC, datetime

        from app.memory_triples import parse_triples

        facts = parse_triples(
            '[{"predicate": "likes", "value": "крепкий кофе", "valid_from": "2026-03", "confidence": 0.9}]',
            source="Люблю крепкий кофе без сахара",
        )

        assert facts[0]["valid_from"].year == datetime.now(UTC).year
