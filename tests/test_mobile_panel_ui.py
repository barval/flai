"""Mobile full-screen panel UI for the Documents/Sessions sidebar.

Structural checks over the shipped JS/HTML/CSS (no live browser), mirroring
the style of test_document_folders_ui.py. The v12.5 mobile redesign replaced
the old collapse toggle with a full-screen panel model:

- clicking the active tab opens the list over the whole chat area
  (class ``panel-open`` on ``.sessions-sidebar``) and clicking it again
  returns to the chat;
- the chat column (header, RLM toggle, message input) is hidden while the
  panel is open;
- the old ``collapse-toggle-mobile`` button is gone.
"""

import pathlib

CHAT_DOCS = pathlib.Path("app/static/js/chat-documents.js").read_text(encoding="utf-8")
CHAT_SESSIONS = pathlib.Path("app/static/js/chat-sessions.js").read_text(encoding="utf-8")
CHAT_INIT = pathlib.Path("app/static/js/chat-init.js").read_text(encoding="utf-8")
CHAT_HTML = pathlib.Path("app/templates/chat.html").read_text(encoding="utf-8")
CHAT_CSS = pathlib.Path("app/static/css/chat.css").read_text(encoding="utf-8")
DARK_CSS = pathlib.Path("app/static/css/dark-theme.css").read_text(encoding="utf-8")


class TestCollapseToggleRemoved:
    def test_no_collapse_toggle_in_template(self):
        assert "collapse-toggle-mobile" not in CHAT_HTML
        assert 'id="collapse-icon"' not in CHAT_HTML

    def test_no_collapse_styles(self):
        assert ".collapse-toggle-mobile" not in CHAT_CSS
        assert ".collapse-icon" not in CHAT_CSS
        assert ".dark-theme .collapse-toggle-mobile" not in DARK_CSS

    def test_no_collapse_js(self):
        assert "initCollapsibleSessions" not in CHAT_SESSIONS
        assert "initCollapsibleSessions" not in CHAT_INIT
        assert "toggleSessions" not in CHAT_SESSIONS
        assert "updateCollapseIcon" not in CHAT_SESSIONS
        assert "sidebar_collapsed_" not in CHAT_SESSIONS
        assert "sidebar_collapsed_" not in CHAT_DOCS


class TestMobilePanel:
    def test_panel_open_class_in_css(self):
        assert ".sessions-sidebar.panel-open" in CHAT_CSS

    def test_panel_hides_chat_column(self):
        # .chat-main must be hidden while the panel is open (mobile media block)
        idx = CHAT_CSS.find(".sessions-sidebar.panel-open ~ .chat-main")
        assert idx != -1
        block = CHAT_CSS[idx : idx + 200]
        assert "display: none" in block

    def test_closed_panel_hides_lists(self):
        # Closed panel (default): lists hidden entirely, chat fills the area
        # from the tab header down to the footer.
        idx = CHAT_CSS.find("    .sessions-list,\n    .documents-list {\n        display: none !important;")
        assert idx != -1
        # The open-panel rule must re-show the active list above the hide rule
        open_idx = CHAT_CSS.find(".sessions-sidebar.panel-open .sessions-list.active")
        assert open_idx != -1
        assert open_idx > idx

    def test_panel_open_toggled_in_switch_view(self):
        assert "classList.add('panel-open'" in CHAT_DOCS
        assert "classList.remove('panel-open'" in CHAT_DOCS

    def test_session_click_closes_panel(self):
        assert "closeMobilePanel" in CHAT_SESSIONS

    def test_new_session_closes_panel(self):
        assert "closeMobilePanel" in CHAT_INIT

    def test_is_mobile_guard(self):
        # Panel logic must only run on mobile viewport
        assert "isMobileViewport" in CHAT_DOCS
        assert "isMobileViewport" in CHAT_SESSIONS

    def test_sidebar_starts_closed_on_mobile(self):
        # No auto-open: the panel must start closed (chat visible) on load
        assert "panel-open" not in CHAT_HTML
