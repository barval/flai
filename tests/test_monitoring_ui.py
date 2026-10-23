# tests/test_monitoring_ui.py
"""Static checks for the Monitoring tab markup, script and catalogs."""

from pathlib import Path


def _admin_html() -> str:
    return Path("app/templates/admin.html").read_text(encoding="utf-8")


def test_monitoring_tab_sits_between_users_and_model_hub():
    html = _admin_html()
    assert html.index('data-tab="users"') < html.index('data-tab="monitoring"') < html.index('data-tab="hub"')


def test_monitoring_controls_and_cards_exist():
    html = _admin_html()
    for element_id in ("monitoring-user", "monitoring-range", "monitoring-cards"):
        assert f'id="{element_id}"' in html
    for metric in (
        "storage",
        "sessions",
        "tasks",
        "outgoing_tokens",
        "incoming_tokens",
        "messages",
        "files",
        "documents",
    ):
        assert f'id="monitoring-chart-{metric}"' in html


def test_monitoring_script_is_loaded():
    assert "js/admin-monitoring.js" in _admin_html()


def test_monitoring_translations_exist_in_both_catalogs():
    en = Path("translations/en/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    ru = Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    for msgid in ("Monitoring", "All users", "No data in this range", "Storage"):
        assert f'msgid "{msgid}"' in en, f"missing msgid {msgid!r} in en"
        assert f'msgid "{msgid}"' in ru, f"missing msgid {msgid!r} in ru"
