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

import re
from pathlib import Path

import pytest


@pytest.mark.unit
def test_base_translations_defaults_do_not_clobber_injected_values():
    src = Path("app/static/js/base-translations.js").read_text(encoding="utf-8")
    for key in ("male_voice", "female_voice", "toggle_voice_gender", "toggle_theme", "loading"):
        assert f"window.TRANSLATIONS['{key}'] = window.TRANSLATIONS['{key}'] ||" in src, (
            f"default for '{key}' must not overwrite an already-injected localized value"
        )


def _template_translation_keys():
    """Every JS key a template injects into ``window.TRANSLATIONS``."""
    keys = set()
    for template in Path("app/templates").glob("*.html"):
        text = template.read_text(encoding="utf-8")
        for start in re.finditer(
            r"window\.TRANSLATIONS\s*=\s*Object\.assign\(\{\},\s*window\.TRANSLATIONS,\s*\{", text
        ):
            rest = text[start.end():]
            end = rest.find("\n});")
            block = rest[: end if end != -1 else len(rest)]
            keys.update(m.group(1) for m in re.finditer(r"^\s*'((?:[^'\\]|\\.)+)'\s*:", block, re.M))
    for script in Path("app/static/js").glob("*.js"):
        source = script.read_text(encoding="utf-8")
        keys.update(m.group(1) for m in re.finditer(r"window\.TRANSLATIONS\['((?:[^'\\]|\\.)+)'\]\s*=", source))
    return keys


@pytest.mark.unit
def test_every_js_key_resolves_to_a_template_translation():
    """A ``t('key')`` without a map entry renders the raw key in every language.

    This is how the multimodal validation alert once printed
    ``multimodal_model_must_be_vision`` to the user.
    """
    known = _template_translation_keys()
    assert len(known) > 500, "the template maps were not parsed"

    missing = {}
    for script in Path("app/static/js").glob("*.js"):
        source = script.read_text(encoding="utf-8")
        for match in re.finditer(r"\bt\(\s*'((?:[^'\\]|\\.)+)'\s*\)", source):
            key = match.group(1)
            if key not in known:
                missing.setdefault(key, script.name)
    assert not missing, f"t() keys without a translation map entry: {missing}"
