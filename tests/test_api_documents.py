"""OpenAI Files-compatible endpoints and FLAI document aliases under `/v1`.

Documents are owner-scoped: listing, metadata, content download and deletion
only ever see resources belonging to the API-key owner, filesystem paths stay
server-side, and uploads reuse the web documents validation chain
(`validate_file`, size/quota limits, image resize/conversion, UUID names,
realpath containment) before enqueueing the existing indexing tasks.
"""

import io
import os
from unittest.mock import Mock

import pytest

from app import db
from app.api_tokens import create_api_token
from app.userdb import create_user

FILES = "/v1/files"
FLAI_DOCS = "/v1/flai/documents"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_user():
    create_user(login="apiuser", password="pw-apiuser-123456", name="API User", language="ru")
    token, _ = create_api_token("apiuser", name="test")
    return token


@pytest.fixture
def client(test_app, api_user):
    return test_app.test_client()


@pytest.fixture
def rag_module(test_app):
    module = Mock()
    module.available = True
    test_app.modules["rag"] = module
    return module


def pdf_bytes():
    return b"%PDF-1.4\n%test document\n"


def png_bytes():
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(output, format="PNG")
    return output.getvalue()


def upload(client, token, *, path=FILES, content=None, filename="doc.pdf", content_type="application/pdf", **form):
    data = {"file": (io.BytesIO(content if content is not None else pdf_bytes()), filename, content_type)}
    data.update(form)
    return client.post(path, data=data, headers=bearer(token), content_type="multipart/form-data")


def save_doc(test_app, user_id, doc_id, filename="note.txt", size=12, ext=".txt", rel_path=None):
    path = rel_path or os.path.join(user_id, doc_id + ext)
    with test_app.app_context():
        db.save_document(user_id, doc_id, filename, size, ext, path)
        db.update_document_index_status(doc_id, db.INDEX_STATUS_PENDING)
    return path


def assert_no_filesystem_leak(response, test_app):
    body = response.get_data(as_text=True)
    assert str(test_app.config["DOCUMENTS_FOLDER"]) not in body
    assert '"file_path"' not in body


def test_files_list_returns_only_caller_documents(client, api_user, test_app):
    save_doc(test_app, "apiuser", "doc-mine", filename="mine.txt")
    save_doc(test_app, "someone-else", "doc-foreign", filename="theirs.txt")
    response = client.get(FILES, headers=bearer(api_user))
    assert response.status_code == 200
    body = response.get_json()
    assert body["object"] == "list"
    assert [entry["id"] for entry in body["data"]] == ["doc-mine"]
    entry = body["data"][0]
    assert entry["object"] == "file"
    assert entry["filename"] == "mine.txt"
    assert entry["bytes"] == 12
    assert entry["purpose"] == "assistants"
    assert entry["index_status"] == db.INDEX_STATUS_PENDING
    assert_no_filesystem_leak(response, test_app)


def test_file_metadata_returns_owner_record_or_404(client, api_user, test_app):
    save_doc(test_app, "apiuser", "doc-meta", filename="report.pdf", size=42, ext=".pdf")
    response = client.get(f"{FILES}/doc-meta", headers=bearer(api_user))
    assert response.status_code == 200
    assert response.get_json()["id"] == "doc-meta"
    assert response.get_json()["purpose"] == "assistants"
    assert_no_filesystem_leak(response, test_app)

    missing = client.get(f"{FILES}/unknown-doc", headers=bearer(api_user))
    assert missing.status_code == 404


def test_upload_requires_file(client, api_user):
    response = client.post(FILES, data={}, headers=bearer(api_user), content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["error"]["type"] == "invalid_request_error"


def test_upload_rejects_unsupported_type(client, api_user):
    response = upload(
        client,
        api_user,
        content=b"MZ\x90\x00binary",
        filename="program.exe",
        content_type="application/octet-stream",
    )
    assert response.status_code == 400


def test_upload_enforces_max_document_size(client, api_user, test_app):
    test_app.config["MAX_DOCUMENT_SIZE_MB"] = 1
    response = upload(client, api_user, content=pdf_bytes() + b"\0" * (1024 * 1536))
    assert response.status_code == 413


def test_upload_enforces_document_quota(client, api_user, test_app, monkeypatch):
    monkeypatch.setattr("app.routes.api_v1.check_document_quota", lambda login: "⚠️ Quota exceeded")
    response = upload(client, api_user)
    assert response.status_code == 413


def test_upload_pdf_stores_relative_path_and_enqueues_index(client, api_user, test_app, monkeypatch):
    enqueue = Mock()
    monkeypatch.setattr(test_app.request_queue, "add_request", enqueue)
    content = pdf_bytes()
    response = upload(client, api_user, content=content, filename="report.pdf")
    assert response.status_code == 200
    body = response.get_json()
    doc_id = body["id"]
    assert body["filename"] == "report.pdf"
    assert body["bytes"] == len(content)
    assert body["purpose"] == "assistants"
    assert body["index_status"] == db.INDEX_STATUS_PENDING
    assert_no_filesystem_leak(response, test_app)

    stored = os.path.join(test_app.config["DOCUMENTS_FOLDER"], "apiuser", f"{doc_id}.pdf")
    assert os.path.isfile(stored)
    with open(stored, "rb") as handle:
        assert handle.read() == content

    assert enqueue.call_count == 1
    kwargs = enqueue.call_args.kwargs
    assert kwargs["user_id"] == "apiuser"
    assert kwargs["session_id"] == ""
    assert kwargs["request_data"]["type"] == "index_document"
    assert kwargs["request_data"]["doc_id"] == doc_id


def test_upload_image_resizes_and_enqueues_describe(client, api_user, test_app, monkeypatch):
    enqueue = Mock()
    monkeypatch.setattr(test_app.request_queue, "add_request", enqueue)
    response = upload(
        client,
        api_user,
        content=png_bytes(),
        filename="photo.png",
        content_type="image/png",
    )
    assert response.status_code == 200
    body = response.get_json()
    doc_id = body["id"]
    stored_dir = os.path.join(test_app.config["DOCUMENTS_FOLDER"], "apiuser")
    assert os.listdir(stored_dir) == [f"{doc_id}.png"]
    assert enqueue.call_args.kwargs["request_data"]["type"] == "describe_document_image"
    assert enqueue.call_args.kwargs["request_data"]["doc_id"] == doc_id


def test_file_content_download_by_owner(client, api_user, test_app):
    content = pdf_bytes()
    response = upload(client, api_user, content=content, filename="report.pdf")
    doc_id = response.get_json()["id"]
    download = client.get(f"{FILES}/{doc_id}/content", headers=bearer(api_user))
    assert download.status_code == 200
    assert download.data == content
    assert "report.pdf" in download.headers.get("Content-Disposition", "")


def test_foreign_file_is_404_everywhere(client, api_user, test_app, rag_module):
    save_doc(test_app, "someone-else", "doc-foreign", filename="theirs.txt")
    for method, path in (
        ("get", f"{FILES}/doc-foreign"),
        ("get", f"{FILES}/doc-foreign/content"),
        ("delete", f"{FILES}/doc-foreign"),
    ):
        response = getattr(client, method)(path, headers=bearer(api_user))
        assert response.status_code == 404
    rag_module.delete_document.assert_not_called()


def test_delete_removes_index_file_and_document(client, api_user, test_app, rag_module):
    response = upload(client, api_user)
    doc_id = response.get_json()["id"]
    stored = os.path.join(test_app.config["DOCUMENTS_FOLDER"], "apiuser", f"{doc_id}.pdf")
    assert os.path.isfile(stored)

    deleted = client.delete(f"{FILES}/{doc_id}", headers=bearer(api_user))
    assert deleted.status_code == 200
    rag_module.delete_document.assert_called_once_with(doc_id, "apiuser")
    assert not os.path.exists(stored)
    assert db.get_document(doc_id, "apiuser") is None

    gone = client.get(f"{FILES}/{doc_id}", headers=bearer(api_user))
    assert gone.status_code == 404


def test_flai_documents_alias_matches_files_routes(client, api_user, test_app, rag_module):
    save_doc(test_app, "apiuser", "doc-alias", filename="alias.txt")

    listed = client.get(FLAI_DOCS, headers=bearer(api_user))
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.get_json()["data"]] == ["doc-alias"]

    enqueue = Mock()
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(test_app.request_queue, "add_request", enqueue)
        uploaded = upload(client, api_user, path=FLAI_DOCS)
        assert uploaded.status_code == 200
        assert enqueue.call_args.kwargs["request_data"]["type"] == "index_document"
    finally:
        monkey.undo()

    doc_id = uploaded.get_json()["id"]
    download = client.get(f"{FLAI_DOCS}/{doc_id}/content", headers=bearer(api_user))
    assert download.status_code == 200
    assert download.data == pdf_bytes()

    deleted = client.delete(f"{FLAI_DOCS}/{doc_id}", headers=bearer(api_user))
    assert deleted.status_code == 200
    assert db.get_document(doc_id, "apiuser") is None
