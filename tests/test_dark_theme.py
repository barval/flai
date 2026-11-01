# tests/test_dark_theme.py
"""Dark-theme guarantees for native form controls.

The browser paints native widgets (autofill, checkboxes, range sliders) with
its own palette unless the page declares a colour scheme; those controls looked
light on the dark UI until the rules below were added.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _dark_css() -> str:
    return Path("app/static/css/dark-theme.css").read_text(encoding="utf-8")


def test_root_declares_the_dark_colour_scheme():
    css = _dark_css()
    block = css.split(".dark-theme {", 1)[1].split("}", 1)[0]
    assert "color-scheme: dark" in block


def test_autofilled_fields_keep_the_themed_background():
    css = _dark_css()
    assert ".dark-theme input:-webkit-autofill" in css
    block = css.split(".dark-theme input:-webkit-autofill,", 1)[1].split("}", 1)[0]
    assert "-webkit-text-fill-color" in block
    assert "transition: background-color 9999s" in block


def test_login_fields_have_a_dark_background():
    css = _dark_css()
    block = css.split(".dark-theme .form-group input {", 1)[1].split("}", 1)[0]
    assert "background-color: #3a3a3a" in block


def test_search_scope_folder_names_stay_readable_in_the_dark_theme():
    """The base rule paints them #444, which disappears on the #2a2a2a popover."""
    base = Path("app/static/css/chat.css").read_text(encoding="utf-8")
    base_block = base.split(".scope-folder-option {", 1)[1].split("}", 1)[0]
    assert "color: #444" in base_block

    css = _dark_css()
    dark_block = css.split(".dark-theme .scope-folder-option {", 1)[1].split("}", 1)[0]
    assert "color: #e0e0e0" in dark_block
