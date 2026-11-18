"""A setting with a closed set of values is picked, not typed.

The panel's schema carried only a `kind`, so `_ENUMS` — the list the save-time validator
enforces — never reached the browser and every enum looked like a free-text box. A typo was
rejected on save, but nothing told the person which words were allowed.
"""

from pathlib import Path

import pytest

from app.settings_registry import _ENUMS, build_schema

ROOT = Path(__file__).resolve().parent.parent

ENUM_KEYS = (
    "SCHEDULER_MISSED_REMINDER",
    "SCHEDULER_MISSED_ACTION",
    "DEPTH_LEVEL",
    "DEPTH_VERIFY",
    "VRAM_CO_RESIDENCY",
)


@pytest.mark.unit
@pytest.mark.parametrize("key", ENUM_KEYS)
def test_an_enum_key_carries_its_choices(test_app, key):
    with test_app.app_context():
        fields = {field["key"]: field for group in build_schema(test_app) for field in group["fields"]}

    assert fields[key]["choices"] == list(_ENUMS[key])


@pytest.mark.unit
def test_a_free_value_key_has_no_choices(test_app):
    with test_app.app_context():
        fields = {field["key"]: field for group in build_schema(test_app) for field in group["fields"]}

    assert fields["SCHEDULER_CATCHUP_MAX"]["choices"] == []


@pytest.mark.unit
def test_the_panel_renders_a_select_for_choices():
    script = (ROOT / "app/static/js/admin-settings.js").read_text(encoding="utf-8")

    assert "field.choices" in script
    assert "createElement('select')" in script
    # A legacy value outside the list must survive an unrelated save instead of being
    # silently replaced by the first option.
    assert "choices.includes(current)" in script
