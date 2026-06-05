from flask import current_app
from flask_babel import force_locale
from flask_babel import gettext as _


class TranslationMixin:
    """Mixin providing translation helper for modules."""

    def _(self, key: str, lang: str = "ru", **kwargs) -> str:
        try:
            with current_app.app_context(), force_locale(lang):
                result = _(key)
            if kwargs:
                result = result.format(**kwargs)
            return result  # type: ignore[no-any-return]
        except RuntimeError:
            return key
