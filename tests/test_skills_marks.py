# tests/test_skills_marks.py
"""What a skill's `allowed-tools` line is allowed to say.

The skill format is shared with Claude Code and opencode, whose tools a FLAI
deployment does not have — so a name this installation never had is not an error.
A name that is one keystroke away from a real tool is, and that is the difference
the check exists for.
"""

from app.skills import classify_allowed_tools

OFFERED = {"web_search", "read_page", "rag_search", "activate_skill"}


def test_a_name_in_the_catalogue_is_offered():
    marks = classify_allowed_tools(("web_search", "rag_search"), OFFERED)

    assert [m.status for m in marks] == ["offered", "offered"]
    assert all(m.suggestion is None for m in marks)


def test_a_typo_is_an_error_class_and_names_the_tool_it_meant():
    marks = classify_allowed_tools(("web-serach",), OFFERED)

    assert marks[0].status == "typo"
    assert marks[0].suggestion == "web_search"


def test_a_case_or_separator_variant_is_a_typo_not_a_foreign_name():
    marks = classify_allowed_tools(("Web_Search",), OFFERED)

    assert marks[0].status == "typo"
    assert marks[0].suggestion == "web_search"


def test_a_foreign_tool_is_only_a_warning():
    marks = classify_allowed_tools(("Read", "Bash(git:*)"), OFFERED)

    assert [m.status for m in marks] == ["foreign", "foreign"]


def test_an_optional_tool_that_is_absent_is_unavailable():
    marks = classify_allowed_tools(("read_page",), {"web_search"})

    assert marks[0].status == "unavailable"
