import base64
import subprocess
from contextlib import nullcontext
from unittest.mock import ANY, Mock, call, patch

import pytest

from app.queue import RedisRequestQueue


@pytest.mark.unit
def test_index_task_schedules_ocr_for_pdf_without_extractable_text(tmp_path):
    queue = RedisRequestQueue.__new__(RedisRequestQueue)
    queue.app = Mock()
    queue.app.config = {"DOCUMENTS_FOLDER": str(tmp_path)}
    queue.app.modules = {"rag": Mock(available=True)}
    queue.app.request_queue.add_request = Mock(return_value=("ocr-task", {}))
    queue.app.logger = Mock()
    queue._publish_document_event = Mock()

    pdf_path = tmp_path / "user" / "scan.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes(b"%PDF-1.4 image-only")
    task = {
        "id": "index-pdf",
        "user_id": "user",
        "lang": "en",
        "user_class": 2,
        "data": {"doc_id": "doc-1", "file_path": str(pdf_path)},
    }

    with (
        patch("app.queue.get_current_time_for_db", return_value="now"),
        patch("app.queue.update_document_index_status") as update_status,
    ):
        queue.app.modules["rag"].index_document.return_value = (False, "Failed to extract text from document")
        result = queue._process_index_task(task)

    assert result == {"success": True, "message": "Scanned PDF queued for OCR", "doc_id": "doc-1"}
    queued_call = queue.app.request_queue.add_request.call_args
    assert queued_call.kwargs["user_id"] == "user"
    assert queued_call.kwargs["request_data"] == {
        "type": "describe_document_pdf",
        "doc_id": "doc-1",
        "file_path": str(pdf_path),
        "preserve_indexing_started_at": True,
    }
    assert update_status.call_count == 1
    assert queue._publish_document_event.call_args_list == [
        call("user", "doc-1", "indexing"),
        call("user", "doc-1", "indexing"),
    ]


@pytest.mark.unit
def test_text_pdf_stays_on_normal_indexing_path():
    queue = RedisRequestQueue.__new__(RedisRequestQueue)
    queue.app = Mock()
    queue.app.modules = {"rag": Mock(available=True)}
    queue.app.modules["rag"].index_document.return_value = (True, "Indexed 1 chunk")
    queue.app.request_queue.add_request = Mock()
    queue.app.logger = Mock()
    queue._publish_document_event = Mock()
    task = {
        "id": "index-text-pdf",
        "user_id": "user",
        "data": {"doc_id": "doc-1", "file_path": "user/text.pdf"},
    }

    with (
        patch("app.queue.get_current_time_for_db", return_value="now"),
        patch("app.queue.update_document_index_status"),
    ):
        result = queue._process_index_task(task)

    assert result["success"] is True
    queue.app.request_queue.add_request.assert_not_called()
    queue._publish_document_event.assert_any_call("user", "doc-1", "indexing")


@pytest.mark.unit
def test_reindex_after_pdf_ocr_preserves_original_processing_start():
    queue = RedisRequestQueue.__new__(RedisRequestQueue)
    queue.app = Mock()
    queue.app.modules = {"rag": Mock(available=True)}
    queue.app.modules["rag"].index_document.return_value = (True, "Indexed 1 chunk")
    queue.app.request_queue.add_request = Mock()
    queue.app.logger = Mock()
    queue._publish_document_event = Mock()
    task = {
        "id": "index-ocr-text",
        "user_id": "user",
        "user_class": 2,
        "lang": "ru",
        "data": {
            "doc_id": "doc-1",
            "file_path": "/documents/user/scan.recognized_text",
            "preserve_indexing_started_at": True,
        },
    }
    update_status = Mock()
    with (
        patch("app.queue.get_current_time_for_db", return_value="must-not-reset"),
        patch("app.queue.update_document_index_status", update_status),
    ):
        result = queue._process_index_task(task)

    assert result["success"] is True
    update_status.assert_any_call("doc-1", "indexing")
    update_status.assert_any_call(
        "doc-1", "indexed", indexed_at="must-not-reset", indexing_started_at=None, embedding_model=ANY
    )
    queue._publish_document_event.assert_any_call("user", "doc-1", "indexing")
    queue._publish_document_event.assert_any_call("user", "doc-1", "indexed")


@pytest.mark.unit
def test_pdf_ocr_task_uses_slow_gpu_queue_and_multimodal_model():
    queue = RedisRequestQueue.__new__(RedisRequestQueue)
    task = {"data": {"type": "describe_document_pdf", "doc_id": "doc-1", "file_path": "user/scan.pdf"}}

    assert queue._classify_task(task) == "slow"
    assert queue._get_model_for_task(task) == "multimodal"


@pytest.mark.unit
def test_pdf_description_task_renders_and_describes_every_page(tmp_path):
    queue = RedisRequestQueue.__new__(RedisRequestQueue)
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 scanned pages")
    multimodal = Mock(available=True)
    multimodal.describe_image_for_rlm.side_effect = [("Text from page one", None), ("Text from page two", None)]
    queue.app = Mock()
    queue.app.config = {"DOCUMENTS_FOLDER": str(tmp_path)}
    queue.app.modules = {"multimodal": multimodal}
    queue.app.request_queue.add_request = Mock()
    queue.app.logger = Mock()
    queue._get_model_name = Mock(return_value="vision-model")

    def run_poppler(args, **kwargs):
        if args[0] == "pdfinfo":
            return subprocess.CompletedProcess(args, 0, stdout="Pages:          2\n", stderr="")
        page_number = int(args[args.index("-f") + 1])
        prefix = args[-1]
        rendered = f"{prefix}.jpg"
        with open(rendered, "wb") as image_file:
            image_file.write(f"page-{page_number}".encode())
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    cursor = Mock()
    connection = Mock()
    connection.cursor.return_value = cursor
    task = {
        "id": "describe-pdf",
        "user_id": "user",
        "lang": "en",
        "user_class": 2,
        "data": {"doc_id": "doc-1", "file_path": str(pdf_path)},
    }

    with (
        patch("app.queue.subprocess.run", side_effect=run_poppler) as run,
        patch("app.database.get_db", return_value=nullcontext(connection)),
        patch("app.queue.get_current_time_for_db", return_value="now"),
        patch("app.queue.update_document_index_status"),
        patch("app.queue.INDEX_STATUS_INDEXING", "indexing"),
    ):
        result = queue._process_describe_document_image_task(task)

    recognized_path = pdf_path.with_suffix(".recognized_text")
    assert result == {"success": True, "message": "PDF pages described and indexing queued", "doc_id": "doc-1"}
    assert (
        recognized_path.read_text(encoding="utf-8")
        == "--- Page 1 ---\nText from page one\n\n--- Page 2 ---\nText from page two"
    )
    assert run.call_count == 3
    assert [call.args[0] for call in multimodal.describe_image_for_rlm.call_args_list] == [
        base64.b64encode(b"page-1").decode("ascii"),
        base64.b64encode(b"page-2").decode("ascii"),
    ]
    queue.app.request_queue.add_request.assert_called_once()
    queued_data = queue.app.request_queue.add_request.call_args.kwargs["request_data"]
    assert queued_data["type"] == "index_document"
    assert queued_data["file_path"] == str(recognized_path)
    cursor.execute.assert_called_once()


@pytest.mark.unit
def test_pdf_description_task_fails_cleanly_when_rendering_fails(tmp_path):
    queue = RedisRequestQueue.__new__(RedisRequestQueue)
    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_bytes(b"not actually a pdf")
    queue.app = Mock()
    queue.app.config = {"DOCUMENTS_FOLDER": str(tmp_path)}
    queue.app.modules = {"multimodal": Mock(available=True)}
    queue.app.request_queue.add_request = Mock()
    queue.app.logger = Mock()
    queue._publish_document_event = Mock()

    with (
        patch(
            "app.queue.subprocess.run",
            return_value=subprocess.CompletedProcess(["pdfinfo"], 1, stdout="", stderr="invalid pdf"),
        ),
        patch("app.queue.update_document_index_status") as update_status,
        patch("app.queue.get_current_time_for_db", return_value="now"),
    ):
        result = queue._process_describe_document_image_task(
            {
                "id": "describe-broken-pdf",
                "user_id": "user",
                "data": {"doc_id": "doc-1", "file_path": str(pdf_path)},
            }
        )

    assert result["success"] is False
    assert "Failed to read PDF page count" in result["error"]
    assert update_status.call_count == 2  # indexing, then failed
    update_status.assert_any_call("doc-1", "indexing")
    update_status.assert_any_call("doc-1", "failed")
    queue._publish_document_event.assert_any_call("user", "doc-1", "failed")
    queue.app.request_queue.add_request.assert_not_called()
