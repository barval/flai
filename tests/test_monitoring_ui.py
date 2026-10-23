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


def test_storage_card_declares_the_history_start_line():
    html = _admin_html()
    assert 'id="monitoring-since-storage"' in html
    assert "'monitoring_since'" in html


def test_charts_keep_their_aspect_ratio():
    html = _admin_html()
    assert html.count('preserveAspectRatio="none"') == 0
    assert html.count('preserveAspectRatio="xMidYMid meet"') == 8


def test_range_buttons_use_short_equal_width_labels():
    html = _admin_html()
    for label in ("1 h", "6 h", "24 h", "7 d", "30 d"):
        assert f"{{{{ _('{label}') }}}}" in html, f"missing range button {label!r}"

    en = Path("translations/en/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    ru = Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
    for msgid, msgstr in (("1 h", "1 ч"), ("6 h", "6 ч"), ("24 h", "24 ч"), ("7 d", "7 д"), ("30 d", "30 д")):
        assert f'msgid "{msgid}"' in en
        assert f'msgid "{msgid}"\nmsgstr "{msgstr}"' in ru

    css = Path("app/static/css/admin.css").read_text(encoding="utf-8")
    assert "flex: 1 1 0" in css
