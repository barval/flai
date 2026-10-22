# tests/test_mixins.py
"""TranslationMixin._() locale resolution.

The helper used to default ``lang`` to the literal "ru" and wrap the lookup in
a NESTED app context, which dropped the caller's ``force_locale`` — so every
call site inside ``with force_locale(lang)`` rendered Russian regardless of the
user profile (live defect: "🎤 Transcribed:" shown in an English profile).
These tests pin the resolution order: explicit lang > ambient locale > "ru".
"""

import pathlib

import pytest
from flask import Flask
from flask_babel import Babel, force_locale

from app.mixins import TranslationMixin

TRANSLATIONS = str(pathlib.Path(__file__).resolve().parent.parent / "translations")


class _Translator(TranslationMixin):
    pass


def _make_app(selector):
    """Minimal app over the real catalogs — no create_app(), no services."""
    app = Flask(__name__)
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = TRANSLATIONS
    Babel(app, locale_selector=selector)
    return app


def _raising_selector():
    """Stand-in for a queue worker: no request context, the selector raises."""
    raise RuntimeError("Working outside of request context")


@pytest.fixture
def translator():
    return _Translator()


def test_ambient_forced_locale_wins(translator):
    app = _make_app(lambda: "ru")
    with app.app_context(), force_locale("en"):
        assert translator._("Transcribed") == "Transcribed"


def test_ambient_forced_locale_can_be_russian(translator):
    app = _make_app(lambda: "ru")
    with app.app_context(), force_locale("ru"):
        assert translator._("Transcribed") == "Распознано"


def test_explicit_lang_beats_the_ambient_locale(translator):
    app = _make_app(lambda: "ru")
    with app.app_context(), force_locale("ru"):
        assert translator._("Transcribed", "en") == "Transcribed"
        assert translator._("Transcribed", lang="en") == "Transcribed"


def test_request_locale_is_used_when_nothing_is_forced(translator):
    app = _make_app(lambda: "en")
    with app.app_context():
        assert translator._("Transcribed") == "Transcribed"


def test_worker_thread_falls_back_to_russian(translator):
    app = _make_app(_raising_selector)
    with app.app_context():
        assert translator._("Transcribed") == "Распознано"
        with force_locale("en"):
            assert translator._("Transcribed") == "Transcribed"


def test_kwargs_are_formatted(translator):
    app = _make_app(lambda: "ru")
    with app.app_context(), force_locale("en"):
        assert translator._("Maximum file size {max_size} MB", max_size=25) == "Maximum file size 25 MB"
