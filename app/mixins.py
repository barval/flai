from flask import current_app
from flask_babel import force_locale, get_locale
from flask_babel import gettext as _


def _ambient_lang() -> str:
    """The locale already active in this thread, or "ru" when there is none.

    A queue worker has no request context, where the locale selector would
    raise. The value is read through ``g`` BEFORE the helper below pushes its
    own app context: a nested app context gets a fresh ``g`` and would lose the
    caller's forced locale.
    """
    try:
        locale = get_locale()
    except RuntimeError:
        return "ru"
    return str(locale) if locale else "ru"


class TranslationMixin:
    """Mixin providing translation helper for modules."""

    def _(self, key: str, lang: str | None = None, **kwargs) -> str:
        """Translate ``key`` for ``lang`` (default: the ambient locale).

        The default used to be the literal "ru", which made every call site
        inside ``with force_locale(lang)`` render Russian regardless of the
        user's profile — the nested app context below drops the caller's forced
        locale, so the ambient value must be read first.
        """
        try:
            if not lang:
                lang = _ambient_lang()
            with current_app.app_context(), force_locale(lang):
                result = _(key)
            if kwargs:
                result = result.format(**kwargs)
            return result  # type: ignore[no-any-return]
        except RuntimeError:
            return key
