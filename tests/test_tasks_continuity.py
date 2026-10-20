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


def test_render_sheet_shape():
    text = render_sheet({"style": "storybook, warm colors",
                         "characters": [{"id": "zhenya", "aliases": ["Женя"], "look": "boy, freckles"}],
                         "places": [], "props": []},
                        goal="Нарисуй Женю у леса")
    assert text.startswith("CONTINUITY (mandatory for this task, keep identical across all images):")
    assert "Style: storybook, warm colors" in text
    assert "Zhenya: boy, freckles" in text
    assert text.count("\n") <= 6  # compact
