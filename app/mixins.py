from flask import current_app
from flask_babel import force_locale, gettext as _


class TranslationMixin:
    """Mixin providing translation helper for modules."""

    def _(self, key: str, lang: str = 'ru', **kwargs) -> str:
        with current_app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)
