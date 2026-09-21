# tests/test_translations.py
"""Tests for the JavaScript translation fallback layer.

``base-translations.js`` ships English defaults for a handful of shared UI
keys. It loads at the END of ``base.html`` — after a template's inline
``window.TRANSLATIONS`` block already injected the localized values. An
unconditional ``window.TRANSLATIONS['x'] = '...'`` would therefore clobber the
user's language with English at runtime (observed: the chat header counter
briefly showed "⏳ Loading..." in a Russian profile on page refresh). Defaults
must be applied only when a key is still missing.
"""

from pathlib import Path

import pytest


@pytest.mark.unit
def test_base_translations_defaults_do_not_clobber_injected_values():
    src = Path("app/static/js/base-translations.js").read_text(encoding="utf-8")
    for key in ("male_voice", "female_voice", "toggle_voice_gender", "toggle_theme", "loading"):
        assert f"window.TRANSLATIONS['{key}'] = window.TRANSLATIONS['{key}'] ||" in src, (
            f"default for '{key}' must not overwrite an already-injected localized value"
        )
