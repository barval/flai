"""Document folders frontend: sidebar list, folders, bulk bar, picker, DnD.

Structural checks over the shipped JS/HTML/CSS (no live browser), mirroring
the style of test_multi_attachment_ui.py."""

import pathlib

CHAT_DOCS = pathlib.Path("app/static/js/chat-documents.js").read_text(encoding="utf-8")
CHAT_HTML = pathlib.Path("app/templates/chat.html").read_text(encoding="utf-8")
CHAT_CSS = pathlib.Path("app/static/css/chat.css").read_text(encoding="utf-8")
DARK_CSS = pathlib.Path("app/static/css/dark-theme.css").read_text(encoding="utf-8")


class TestFolderMarkup:
    def test_add_folder_button_in_sidebar(self):
        assert 'id="add-folder-button"' in CHAT_HTML
        assert "📁" in CHAT_HTML

    def test_folder_css_classes_exist(self):
        for cls in (
            ".document-folder",
            ".document-folder-header",
            ".document-folder-docs",
            ".folder-chevron",
            ".folder-picker-menu",
            ".bulk-documents-bar",
            ".drag-over",
        ):
            assert cls in CHAT_CSS

    def test_document_actions_css(self):
        assert ".move-document-button" in CHAT_CSS
        assert ".document-checkbox" in CHAT_CSS


class TestFolderFrontendLogic:
    def test_load_parses_new_payload_shape(self):
        assert "payload.folders" in CHAT_DOCS
        assert "payload.documents" in CHAT_DOCS

    def test_folder_render(self):
        assert "function renderFolderItem" in CHAT_DOCS
        assert "📁" in CHAT_DOCS  # collapsed chevron
        assert "📂" in CHAT_DOCS  # expanded chevron
        assert 'class="folder-check"' in CHAT_DOCS
        assert "data-folder-id" in CHAT_DOCS

    def test_collapse_state_persists(self):
        assert "function toggleFolderCollapse" in CHAT_DOCS
        assert "docfolder_collapsed_" in CHAT_DOCS
        assert "localStorage" in CHAT_DOCS

    def test_bulk_selection_and_tristate(self):
        assert "selectedDocIds" in CHAT_DOCS
        assert 'class="doc-check"' in CHAT_DOCS
        assert "bulk-documents-bar" in CHAT_DOCS
        assert "indeterminate" in CHAT_DOCS
        assert "function clearDocumentSelection" in CHAT_DOCS

    def test_move_actions_exist(self):
        assert "move-document-button" in CHAT_DOCS
        assert "/api/documents/${docId}/folder" in CHAT_DOCS
        assert "PATCH" in CHAT_DOCS
        assert "function moveSelectedDocuments" in CHAT_DOCS
        assert "/api/documents/move" in CHAT_DOCS

    def test_folder_picker(self):
        assert "function showFolderPicker" in CHAT_DOCS
        assert "folder-picker-menu" in CHAT_DOCS
        assert "folder_move_to_root" in CHAT_DOCS

    def test_upload_target_folder(self):
        assert "formData.append('folder_id', folderId)" in CHAT_DOCS

    def test_folder_crud_calls(self):
        assert "function createFolder" in CHAT_DOCS
        assert "function renameFolder" in CHAT_DOCS
        assert "function deleteFolder" in CHAT_DOCS
        assert "/api/document-folders" in CHAT_DOCS
        assert "folder_delete_cascade_confirm" in CHAT_DOCS

    def test_drag_and_drop(self):
        assert "function attachDocumentDragAndDrop" in CHAT_DOCS
        assert 'draggable="true"' in CHAT_DOCS
        assert "dataTransfer.setData" in CHAT_DOCS
        assert "onDocumentDrop" in CHAT_DOCS

    def test_js_translation_keys_shipped(self):
        for key in (
            "folder_new",
            "folder_rename",
            "folder_delete",
            "folder_clear_selection",
            "folder_move_to_root",
            "documents_selected_count",
            "folder_delete_confirm",
            "folder_delete_cascade_confirm",
        ):
            assert key in CHAT_HTML


class TestBulkBar:
    def test_bulk_buttons_have_ids(self):
        assert 'id="bulk-move-button"' in CHAT_DOCS
        assert 'id="bulk-clear-button"' in CHAT_DOCS

    def test_bulk_move_icon(self):
        # arrow + folder icons replace the «Move to…» text; tooltip stays
        assert "title=\"${t('folder_move')}\">➤ 📂</button>" in CHAT_DOCS

    def test_bulk_clear_icon(self):
        # lone ✖ replaces the «Clear selection» text; tooltip stays
        assert "title=\"${t('folder_clear_selection')}\">✖</button>" in CHAT_DOCS

    def test_text_labels_removed_from_bulk_buttons(self):
        assert "folder_move_selected" not in CHAT_DOCS

    def test_move_document_button_arrow(self):
        # per-document move button gets the same arrow + folder icons
        assert 'move-document-button" title="${t(\'folder_move\')}">➤ 📂</button>' in CHAT_DOCS


class TestFolderDialog:
    def test_dialog_markup_present(self):
        assert 'id="folder-name-modal"' in CHAT_HTML
        assert 'id="folder-name-input"' in CHAT_HTML
        assert 'id="folder-name-error"' in CHAT_HTML
        assert 'id="folder-name-cancel"' in CHAT_HTML
        assert 'id="folder-name-submit"' in CHAT_HTML

    def test_open_close_functions(self):
        assert "function openFolderNameDialog" in CHAT_DOCS
        assert "function closeFolderNameDialog" in CHAT_DOCS

    def test_no_native_prompt(self):
        assert "prompt(" not in CHAT_DOCS

    def test_inline_error_setter(self):
        assert "folder-name-error" in CHAT_DOCS
        assert "showFolderNameError" in CHAT_DOCS

    def test_dialog_translation_keys_shipped(self):
        for key in ("folder_create_title", "folder_rename_title", "folder_name_label"):
            assert key in CHAT_HTML

    def test_dialog_css_rules(self):
        assert ".folder-name-modal" in CHAT_CSS
        assert ".folder-name-input" in CHAT_CSS
        assert ".folder-name-error" in CHAT_CSS


class TestFolderDarkTheme:
    REQUIRED_DARK_RULES = (
        ".dark-theme .document-folder",
        ".dark-theme .document-folder-header",
        ".dark-theme .document-folder-header.drag-over",
        ".dark-theme .folder-meta",
        ".dark-theme .bulk-documents-bar",
        ".dark-theme .bulk-label",
        ".dark-theme .bulk-move-button",
        ".dark-theme .folder-picker-menu",
        ".dark-theme .folder-picker-item",
    )

    def test_folder_styles_covered_in_dark_theme(self):
        for rule in self.REQUIRED_DARK_RULES:
            assert rule in DARK_CSS, rule

    def test_folder_name_dialog_covered_in_dark_theme(self):
        assert ".dark-theme .folder-name-content" in DARK_CSS
        assert ".dark-theme .folder-name-input" in DARK_CSS
