"""An SVG is a generated document: validated on write, previewed as an image, not indexed."""

import pytest

from app import db, documents_write
from app.documents_write import DocumentWriteError, write_document_file

pytestmark = pytest.mark.requires_db

LOGIN = "svg_writer_test"
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" width="10" height="10">'
    '<title>Dot</title><rect width="10" height="10" fill="#2563eb"/></svg>'
)


def cleanup():
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM documents WHERE user_id = %s", (LOGIN,))
        c.execute("DELETE FROM user_storage WHERE user_id = %s", (LOGIN,))


@pytest.fixture
def writer_env(test_app, tmp_path, monkeypatch):
    root = tmp_path / "documents"
    monkeypatch.setattr(documents_write, "_documents_root", lambda: str(root))
    monkeypatch.setitem(test_app.config, "DOCUMENTS_FOLDER", str(root))
    with test_app.app_context():
        cleanup()
        yield root
        cleanup()


def test_svg_is_saved_and_previewed_as_an_image(writer_env, test_app):
    doc = write_document_file(LOGIN, "Точка", SVG, fmt="svg")

    assert doc.filename == "Точка.svg"
    assert (writer_env / LOGIN / f"{doc.id}.svg").read_text(encoding="utf-8").strip() == SVG
    assert db.get_document(doc.id, LOGIN)["file_ext"] == ".svg"

    client = test_app.test_client()
    with client.session_transaction() as sess:
        sess["login"] = LOGIN
        sess["user_class"] = 100
        sess["language"] = "en"
    payload = client.get(f"/api/documents/{doc.id}/preview").get_json()
    assert payload["kind"] == "binary"
    assert payload["mime"] == "image/svg+xml"

    served = client.get(payload["url"])
    assert served.status_code == 200
    assert served.headers["Content-Type"].startswith("image/svg+xml")
    assert "sandbox" in served.headers["Content-Security-Policy"]


def test_invalid_xml_is_refused(writer_env):
    with pytest.raises(DocumentWriteError) as exc:
        write_document_file(LOGIN, "Broken", "<svg><rect></svg>", fmt="svg")
    assert exc.value.code == "svg_invalid"


def test_executable_content_is_refused(writer_env):
    with pytest.raises(DocumentWriteError) as exc:
        write_document_file(LOGIN, "Bad", "<svg><script>alert(1)</script></svg>", fmt="svg")
    assert exc.value.code == "svg_unsafe"


def test_append_is_refused(writer_env):
    with pytest.raises(DocumentWriteError) as exc:
        write_document_file(LOGIN, "Dot", SVG, fmt="svg", append=True)
    assert exc.value.code == "append_svg"


def test_the_index_switch_is_refused(writer_env, test_app):
    doc = write_document_file(LOGIN, "Dot", SVG, fmt="svg")
    client = test_app.test_client()
    with client.session_transaction() as sess:
        sess["login"] = LOGIN
        sess["user_class"] = 100
        sess["language"] = "en"
    response = client.post(f"/api/documents/{doc.id}/index", json={"enabled": True})
    assert response.status_code == 400
    assert "SVG" in response.get_json()["error"]
