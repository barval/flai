"""Render the real Jinja templates without creating the application."""

from __future__ import annotations

import gettext
import html as html_module

from flask import Flask, render_template
from jinja2 import ChainableUndefined

from demo.io import ROOT, read_app_version

TEMPLATE_DIR = ROOT / "app" / "templates"
TRANSLATIONS_DIR = ROOT / "translations"

LOGIN_INPUT_ANCHOR = '<input type="text" id="login" name="login" required autofocus>'
PASSWORD_INPUT_ANCHOR = '<input type="password" id="password" name="password" required>'
LOGIN_FORM_ANCHOR = '<form method="POST" id="login-form">'

BASE_CONTEXT = {
    "branding_logo_url": None,
    "branding_site_name": None,
    "session": {},
}

TEMPLATES = {"chat": "chat.html", "admin": "admin.html"}


class RenderError(RuntimeError):
    """Raised when a template no longer contains the anchor the demo depends on."""


def _translations(lang: str):
    return gettext.translation("messages", localedir=str(TRANSLATIONS_DIR), languages=[lang])


def jinja_app(lang: str) -> Flask:
    """A Flask app that can render the templates but owns no database, queue or modules."""
    app = Flask("demo", template_folder=str(TEMPLATE_DIR), static_folder=None)
    app.config["APP_VERSION"] = read_app_version()
    app.jinja_env.undefined = ChainableUndefined
    translations = _translations(lang)
    app.jinja_env.globals.update(
        {
            "_": translations.gettext,
            "gettext": translations.gettext,
            "ngettext": translations.ngettext,
            "csrf_token": lambda: "demo-token",
        }
    )
    app.add_url_rule("/static/<path:filename>", "static", lambda filename: "", methods=["GET"])
    app.add_url_rule("/logout", "auth.logout", lambda: "", methods=["POST"])
    return app


def render_app(lang: str, kind: str, context: dict) -> str:
    """Render the chat or admin page exactly as the application would."""
    if kind not in TEMPLATES:
        raise ValueError(f"Unknown demo page kind: {kind}")
    app = jinja_app(lang)
    with app.test_request_context("/"):
        return render_template(TEMPLATES[kind], **{**BASE_CONTEXT, **context})


def _replace_once(html: str, anchor: str, replacement: str) -> str:
    if html.count(anchor) != 1:
        raise RenderError(f"Login template anchor not found exactly once: {anchor}")
    return html.replace(anchor, replacement, 1)


def render_login(lang: str, login: str = "user") -> str:
    """Render the login page with filled, locked fields and a demo submit button."""
    value = html_module.escape(login, quote=True)
    app = jinja_app(lang)
    with app.test_request_context("/login"):
        html = render_template("login.html", **BASE_CONTEXT)
    html = _replace_once(
        html,
        LOGIN_INPUT_ANCHOR,
        f'<input type="text" id="login" name="login" value="{value}" required disabled>',
    )
    html = _replace_once(
        html,
        PASSWORD_INPUT_ANCHOR,
        f'<input type="password" id="password" name="password" value="{value}" required disabled>',
    )
    return _replace_once(html, LOGIN_FORM_ANCHOR, '<form method="POST" id="login-form" data-demo-login="1">')
