"""Multi-attachment frontend: chips, multiple input, translated cap message.

Structural checks over the shipped JS/HTML/CSS (no live browser), mirroring
the style of test_crawl_ui.py."""

import pathlib

CHAT_INIT = pathlib.Path("app/static/js/chat-init.js").read_text(encoding="utf-8")
CHAT_MESSAGES = pathlib.Path("app/static/js/chat-messages.js").read_text(encoding="utf-8")
CHAT_HTML = pathlib.Path("app/templates/chat.html").read_text(encoding="utf-8")
CHAT_CSS = pathlib.Path("app/static/css/chat.css").read_text(encoding="utf-8")
DARK_CSS = pathlib.Path("app/static/css/dark-theme.css").read_text(encoding="utf-8")
CONSTANTS = pathlib.Path("app/static/js/chat-constants.js").read_text(encoding="utf-8")


class TestAttachmentChips:
    def test_file_input_allows_multiple(self):
        assert 'id="file-input"' in CHAT_HTML
        input_line = next(ln for ln in CHAT_HTML.split("\n") if 'id="file-input"' in ln)
        assert "multiple" in input_line

    def test_max_chat_images_exposed_to_frontend(self):
        assert "window.FLAI_MAX_CHAT_IMAGES" in CHAT_HTML
        assert "FLAI_MAX_CHAT_IMAGES" in CONSTANTS

    def test_chip_render_exists_and_uses_cap(self):
        assert "function renderAttachmentChips" in CHAT_INIT
        assert "MAX_CHAT_IMAGES" in CHAT_INIT
        assert "function addAttachedFile" in CHAT_INIT
        assert "max_images_reached" in CHAT_INIT

    def test_chip_emoji_and_thumbs(self):
        # documents/audio chips carry an emoji; images get a thumbnail
        assert "docEmoji" in CHAT_INIT
        assert "attachment-chip-thumb" in CHAT_INIT
        assert "'🎵'" in CHAT_INIT or '"🎵"' in CHAT_INIT
        assert "'📄'" in CHAT_INIT or '"📄"' in CHAT_INIT

    def test_send_appends_every_file(self):
        send_block = CHAT_INIT[
            CHAT_INIT.index("const sendToServer") : CHAT_INIT.index(
                "if (window.IS_RELOADING) return;\n\n                // Check if response"
            )
        ]
        assert "for (const f of tempFiles) formData.append('file', f);" in send_block

    def test_display_renders_extra_attachments(self):
        assert "__extraAttachments" in CHAT_MESSAGES
        assert "extra-attachments" in CHAT_MESSAGES

    def test_css_for_chips_light_and_dark(self):
        assert ".attachment-chip" in CHAT_CSS
        assert ".attachment-chip-thumb" in CHAT_CSS
        assert ".extra-attachments" in CHAT_CSS
        assert ".dark-theme .attachment-chip" in DARK_CSS

    def test_chip_height_matches_send_button_area(self):
        # thumbnails stay inside the send-button height (~38px chip, 30px thumb)
        m = next(ln for ln in CHAT_CSS.split("\n") if ln.strip().startswith("height: 38px"))
        assert "38px" in m


class TestTranslations:
    def test_new_keys_in_both_catalogs(self):
        ru = pathlib.Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
        en = pathlib.Path("translations/en/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
        for key in ("max_images_reached", "remove_file"):
            assert f'msgid "{key}"' in ru, key
            assert f'msgid "{key}"' in en, key

    def test_ru_cap_message_translated(self):
        ru = pathlib.Path("translations/ru/LC_MESSAGES/messages.po").read_text(encoding="utf-8")
        idx = ru.index('msgid "max_images_reached"')
        block = ru[idx : idx + 120]
        assert "msgstr" in block
        msgstr = block.split("msgstr", 1)[1]
        # must not be empty
        assert msgstr.strip().split("\n", 1)[0].strip().strip('"')


class TestNoCyrillicInJs:
    def test_chat_init_no_cyrillic(self):
        import re

        cyr = [ln for ln in CHAT_INIT.split("\n") if re.search(r"[А-Яа-яЁё]", ln)]
        assert cyr == [], cyr[:3]

    def test_chat_messages_no_cyrillic(self):
        import re

        cyr = [ln for ln in CHAT_MESSAGES.split("\n") if re.search(r"[А-Яа-яЁё]", ln)]
        assert cyr == [], cyr[:3]
