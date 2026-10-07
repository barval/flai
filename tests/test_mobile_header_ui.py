"""Mobile header slim-down UI (v12.5).

Structural checks over the shipped JS/HTML/CSS (no live browser):

- language dropdown shows only «Ру»/«En» on mobile (compact option texts);
- the logout button becomes a small square icon button next to the language
  switcher;
- the voice-gender and theme buttons move into the footer row on mobile
  (left / right corner) via DOM relocation in header.js;
- the mobile header is a single ~38 px row instead of the old 75 px two-row
  layout.
"""

import pathlib

BASE_HTML = pathlib.Path("app/templates/base.html").read_text(encoding="utf-8")
HEADER_CSS = pathlib.Path("app/static/css/header-footer.css").read_text(encoding="utf-8")
HEADER_JS = pathlib.Path("app/static/js/header.js").read_text(encoding="utf-8")
ADMIN_CSS = pathlib.Path("app/static/css/admin.css").read_text(encoding="utf-8")


class TestCompactLanguageOptions:
    def test_options_carry_short_labels(self):
        assert 'data-short="Ру"' in BASE_HTML
        assert 'data-short="En"' in BASE_HTML

    def test_custom_dropdown_in_template(self):
        # Mobile uses toggle + menu (menu lists the FULL names)
        assert 'id="lang-toggle"' in BASE_HTML
        assert 'id="lang-menu"' in BASE_HTML
        assert 'class="lang-option"' in BASE_HTML

    def test_js_compact_switch(self):
        assert "applyCompactLangOptions" in HEADER_JS
        assert "initLangDropdown" in HEADER_JS
        assert "lang-option" in HEADER_JS

    def test_toggle_label_from_data_short(self):
        # «Ру ▾» / «En ▾» come from the options' data-short (no toUpperCase
        # on the lang value — that produced "RU"/"EN")
        assert "selected.dataset.short" in HEADER_JS
        assert "dataset.lang.toUpperCase()" not in HEADER_JS

    def test_menu_starts_closed(self):
        # applyCompactLangOptions must not open the menu on layout switches
        assert "menu.hidden = true" in HEADER_JS
        assert "menu.hidden = !compact" not in HEADER_JS

    def test_js_mobile_media_guard(self):
        assert "matchMedia" in HEADER_JS

    def test_relocation_works_without_voice(self):
        # Guest (login) page: theme relocation must not require the voice
        # switcher to exist
        assert "if (!theme) return;" in HEADER_JS


class TestLogoutIconButton:
    def test_logout_button_has_icon_and_text_spans(self):
        assert 'class="logout-icon"' in BASE_HTML
        assert 'class="logout-text"' in BASE_HTML

    def test_mobile_square_logout(self):
        # The mobile block sits at the END of the file (after the base rules)
        # so the cascade actually applies it.
        mobile_idx = HEADER_CSS.rfind("@media (max-width: 768px)")
        block = HEADER_CSS[mobile_idx:]
        assert "height: 38px" in block
        assert ".logout-button" in block
        assert "width: 28px" in block
        assert "height: 28px" in block
        # The mobile block must come after the base logout-button rule
        base_logout = HEADER_CSS.find(".logout-button {")
        assert base_logout < mobile_idx

    def test_desktop_hides_icon(self):
        # Desktop rule hides the icon; the mobile media block shows it.
        # The mobile block comes last in the file, so match by context.
        mobile_idx = HEADER_CSS.rfind("@media (max-width: 768px)")
        mobile_block = HEADER_CSS[mobile_idx:]
        icon_mobile = mobile_block.find(".logout-button .logout-icon")
        assert icon_mobile != -1
        assert "display: inline" in mobile_block[icon_mobile : icon_mobile + 60]
        assert ".logout-button .logout-text" in HEADER_CSS
        text_rule = HEADER_CSS.find(".logout-button .logout-text")
        text_block = HEADER_CSS[max(0, text_rule - 2000) : text_rule]
        assert "@media (max-width: 768px)" in text_block
        # Outside the mobile block the icon stays hidden (desktop)
        base_rule = HEADER_CSS.find(".logout-button .logout-icon")
        assert base_rule < mobile_idx
        assert "display: none" in HEADER_CSS[base_rule : base_rule + 60]


class TestFooterRelocation:
    def test_js_moves_controls_to_footer(self):
        assert "footer.appendChild" in HEADER_JS or "appendChild(" in HEADER_JS
        assert "voice-gender-switcher" in HEADER_JS
        assert "theme-switcher" in HEADER_JS

    def test_js_restores_controls_on_desktop(self):
        assert "header-row" in HEADER_JS or "row2" in HEADER_JS

    def test_footer_relative_and_corners(self):
        assert "position: relative" in HEADER_CSS
        assert ".voice-gender-switcher" in HEADER_CSS
        assert ".theme-switcher" in HEADER_CSS

    def test_menu_opens_leftwards(self):
        # The switcher hugs the right screen edge: the menu must anchor
        # right: 0, not left: 0 (which pushed it off-screen)
        menu_idx = HEADER_CSS.find(".lang-menu {")
        assert menu_idx != -1
        menu_block = HEADER_CSS[menu_idx : menu_idx + 300]
        assert "right: 0" in menu_block

    def test_hidden_attribute_honoured(self):
        # Author display rules beat the UA [hidden] style — explicit rules
        # must re-hide the mobile-only toggle and menu on desktop
        assert ".lang-toggle[hidden]" in HEADER_CSS
        assert ".lang-menu[hidden]" in HEADER_CSS

    def test_dropdown_dark_theme(self):
        # Dark-theme rules live in header-footer.css right after the dropdown
        assert ".dark-theme .lang-menu {" in HEADER_CSS
        assert ".dark-theme .lang-toggle {" in HEADER_CSS


class TestDesktopHeaderOrder:
    """Desktop: voice + theme sit in the first row BEFORE the user name;
    logout is the rightmost control. Mobile keeps the footer relocation."""

    def _row1_segment(self):
        start = BASE_HTML.find("header-row row1")
        end = BASE_HTML.find("header-row row2")
        assert start != -1 and end != -1 and start < end
        return BASE_HTML[start:end]

    def test_voice_theme_name_lang_logout_order(self):
        row1 = self._row1_segment()
        voice_idx = row1.find("voice-gender-switcher")
        theme_idx = row1.find("theme-switcher")
        name_idx = row1.find("user-name")
        lang_idx = row1.find("language-switcher")
        logout_idx = row1.find("logout-button")
        for idx in (voice_idx, theme_idx, name_idx, lang_idx, logout_idx):
            assert idx != -1, "control missing from header row 1"
        assert voice_idx < theme_idx < name_idx < lang_idx < logout_idx

    def test_row2_holds_no_switchers(self):
        start = BASE_HTML.find("header-row row2")
        panel = BASE_HTML.find("api-keys-panel", start)
        row2 = BASE_HTML[start:panel]
        assert "voice-gender-switcher" not in row2
        assert "theme-switcher" not in row2

    def test_js_restores_before_user_name(self):
        # Desktop restore must insert the switchers back before the
        # user-name button (id api-keys-btn), not into the old row2
        assert "querySelector('.header-row.row1')" in HEADER_JS
        assert "getElementById('api-keys-btn')" in HEADER_JS

    def test_js_guest_restore_into_header(self):
        # The login page has no row1 — the theme switcher returns to <header>
        assert "querySelector('header')" in HEADER_JS


class TestSlimHeader:
    def test_mobile_header_height(self):
        assert "height: 38px" in HEADER_CSS

    def test_mobile_logo_shrinks(self):
        assert ".header-logo" in HEADER_CSS

    def test_admin_mobile_offsets_follow_header(self):
        # 38px header + 30px footer + 20px viewport error
        assert "calc(100vh - 88px)" in ADMIN_CSS
