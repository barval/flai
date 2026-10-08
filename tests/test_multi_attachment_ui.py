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

    def test_extra_attachments_skip_parts_without_source(self):
        # history reloads strip file_data for stored parts; a part with
        # neither file_path nor file_data must not become a broken data: URL
        m = CHAT_MESSAGES.index("window.__extraAttachments;")
        block = CHAT_MESSAGES[m : m + 900]
        assert "const hasSource = part.file_path || part.file_data;" in block
        assert "if (!hasSource) continue;" in block

    def test_optimistic_render_includes_multi_queue(self):
        # the no-legacy-file branch reads queued images and passes them into
        # the optimistic user message (images visible before the POST returns)
        assert "readExtraAsBase64" in CHAT_INIT
        assert "displayUserMessage(null, null, null, null, extraParts)" in CHAT_INIT

    def test_first_content_attachment_skipped_only_when_legacy_renders_it(self):
        # displayMessage splits content attachments into the legacy first one
        # (rendered from the fileData/filePath parameters) and the inline extras.
        # Skipping index 0 is only correct while those parameters are present:
        # queued images never occupy the legacy slot (chat-init.js file-input
        # handler keeps non-image files there), so an unconditional slice(1)
        # lost image #1 from every optimistic multi-image render — 4 attached,
        # 3 shown — and the HTML export mirrors the live DOM, so it dropped it too.
        start = CHAT_MESSAGES.index("const contentAttachments = []")
        block = CHAT_MESSAGES[start : start + 1600]
        assert "fileData || filePath" in block, (
            "extras must be gated on the legacy render: without the "
            "fileData/filePath parameters nothing renders the first attachment"
        )
        assert "contentAttachments.slice(1)" in block

    def test_legacy_slot_send_renders_queued_images_too(self):
        # voice/doc + images: the legacy branch called displayUserMessage
        # WITHOUT extraParts, so the queued images never entered the optimistic
        # content JSON. They were uploaded and shown after F5, but the tempId
        # relabel (and the user SSE echo guard) never re-renders from server
        # content, so the images stayed invisible for the whole session.
        start = CHAT_INIT.index("reader.onload = async function")
        block = CHAT_INIT[start : CHAT_INIT.index("sendToServer();", start)]
        assert "await readExtraAsBase64(tempFiles)" in block, (
            "the legacy-slot send must read the queued images and pass them "
            "as extraParts, otherwise they never reach the optimistic render"
        )


class TestExportAllAttachments:
    def test_export_collects_every_media_element(self):
        # Save-as-HTML used querySelector (first match only), so images 2..N
        # of a multi-attachment message were silently dropped from the export.
        export_src = pathlib.Path("app/static/js/chat-export.js").read_text(encoding="utf-8")
        assert "querySelectorAll('.attached-image')" in export_src
        assert "querySelectorAll('audio')" in export_src
        assert "querySelectorAll('video')" in export_src
        # per-message media info keeps arrays, not single slots
        assert "images: []" in export_src
        assert "audios: []" in export_src
        assert "videos: []" in export_src
        # render loops over the arrays
        assert "for (const image of msg.media.images)" in export_src
        assert "for (const audio of msg.media.audios)" in export_src
        assert "for (const video of msg.media.videos)" in export_src

    def test_export_footer_mirrors_site_about_dialog(self):
        export_src = pathlib.Path("app/static/js/chat-export.js").read_text(encoding="utf-8")
        # one-line brand label like the live site (no retired two-line footer)
        assert "footer-brand" in export_src
        assert "footer_short_name" in export_src
        assert "footer-line1" not in export_src and "footer-line2" not in export_src
        # embedded About dialog: full name, version, GitHub link, copyright
        assert "export-about-modal" in export_src
        assert "footer_text" in export_src
        assert "footer_copyright" in export_src
        assert "https://github.com/barval/flai" in export_src
        # logo inside the dialog, inline open/close JS with Escape
        assert "about-logo" in export_src
        assert "about-modal-close" in export_src
        assert 'e.key === "Escape"' in export_src

    def test_export_dialog_keys_available_to_chat_js(self):
        # the export reads footer_short_name/footer_about_hint/close_about
        # through t() — chat.html must inject all three into TRANSLATIONS
        assert "'footer_short_name'" in CHAT_HTML
        assert "'footer_about_hint'" in CHAT_HTML
        assert "'close_about'" in CHAT_HTML

    def test_export_css_has_footer_brand(self):
        export_css = pathlib.Path("app/static/css/export.css").read_text(encoding="utf-8")
        assert "footer .footer-brand" in export_css

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
