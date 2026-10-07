"""Document folders frontend: sidebar list, folders, bulk bar, picker, DnD.

Structural checks over the shipped JS/HTML/CSS (no live browser), mirroring
the style of test_multi_attachment_ui.py."""

import pathlib

CHAT_DOCS = pathlib.Path("app/static/js/chat-documents.js").read_text(encoding="utf-8")
CHAT_HTML = pathlib.Path("app/templates/chat.html").read_text(encoding="utf-8")
CHAT_CSS = pathlib.Path("app/static/css/chat.css").read_text(encoding="utf-8")


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
            "folder_name_prompt",
            "folder_rename",
            "folder_delete",
            "folder_move_selected",
            "folder_clear_selection",
            "folder_move_to_root",
            "documents_selected_count",
            "folder_delete_confirm",
            "folder_delete_cascade_confirm",
        ):
            assert key in CHAT_HTML
