"""Static guards for the document preview (main window, Documents tab).

Mobile Chrome cannot render a PDF inside an iframe, so the preview renders
server-side page images there; these checks keep the route, the renderer and
the header ownership wired together.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTES = (ROOT / "app/routes/documents.py").read_text(encoding="utf-8")
JS = (ROOT / "app/static/js/chat-documents.js").read_text(encoding="utf-8")
EVENTS = (ROOT / "app/static/js/events.js").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/css/chat.css").read_text(encoding="utf-8")


def test_pdf_preview_reports_pages_and_renders_them_server_side():
    assert '@bp.route("/documents/<doc_id>/preview/pdf-page/<int:page>"' in ROUTES
    assert "PREVIEW_PDF_MAX_PAGES = 30" in ROUTES
    assert '"kind": "pdf"' in ROUTES
    assert '"page_url":' in ROUTES
    assert '"pdftoppm"' in ROUTES


def test_mobile_preview_uses_page_images_instead_of_an_iframe():
    assert "data.kind === 'pdf'" in JS
    assert "isMobileViewport()" in JS
    assert "document-preview-page" in JS
    assert "data.page_url" in JS
    assert "document_open_pdf" in JS


def test_preview_owns_the_header_while_a_document_is_open():
    assert "window.isDocumentPreviewActive = isDocumentPreviewActive" in JS
    assert EVENTS.count("if (typeof window.isDocumentPreviewActive === 'function'") == 2
    assert "document.body.classList.add('document-preview-mode')" in JS
    # The title gets every pixel up to the 💾 button on desktop and mobile.
    assert "body.document-preview-mode .chat-header h2" in CSS
    assert "flex: 1 1 auto" in CSS
