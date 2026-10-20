# tests/test_tasks_continuity.py
"""Pure-logic tests for the visual continuity sheet helpers (app/tasks/continuity.py)."""

from app.tasks.continuity import (
    render_sheet,
    sanitize_reference_markers,
    select_cards,
)


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
