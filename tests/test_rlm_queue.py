import json
import threading
from unittest.mock import Mock, patch

import pytest

from app.queue import RedisRequestQueue
from app.resource_manager import ResourceManager
from modules.rlm import RlmResult, RlmTraceStep


@pytest.mark.unit
def test_process_rlm_task_adds_image_description_to_corpus():
    q = RedisRequestQueue.__new__(RedisRequestQueue)
    q._publish_stream_event = lambda task, event_type, extra=None: None
    q._is_task_cancelled = Mock(return_value=False)
    q._get_model_name = Mock(return_value="reasoning")
    q._save_and_respond = Mock(return_value={"status": "ok"})
    q.redis = Mock()
    app = Mock()
    app.modules = {"base": Mock(), "multimodal": Mock()}
    app.config = {"DOCUMENTS_FOLDER": "/tmp/documents"}
    q.app = app

    task = {
        "id": "rlm-img-1",
        "data": {
            "type": "rlm_analysis",
            "text": "What is in the photo?",
            "doc_ids": [],
            "file_data": "BASE64",
            "file_type": "image/jpeg",
            "file_name": "photo.jpg",
        },
        "session_id": "s1",
        "user_id": "u1",
        "lang": "en",
    }
    rm = Mock()
    rm.ensure_vram_for = Mock(return_value=True)
    rm.ensure_vram_for_reasoning = Mock(return_value=True)
    run_mock = Mock(return_value=RlmResult(answer="ok", trace=[], steps=1))
    with (
        patch("modules.rlm.RlmModule") as mock_rlm_module,
        patch("app.resource_manager.get_resource_manager", return_value=rm),
    ):
        mock_rlm_module.return_value.run = run_mock
        app.modules["multimodal"].describe_image_for_rlm = Mock(return_value=("A cat on a sofa", None))
        result = q._process_rlm_task(task)

    assert result["status"] == "ok"
    corpus = run_mock.call_args.kwargs["corpus"]
    assert corpus == {"Изображение (photo.jpg)": "A cat on a sofa"}
    app.modules["multimodal"].describe_image_for_rlm.assert_called_with("BASE64", "en")


@pytest.mark.unit
def test_process_rlm_task_reports_image_description_error():
    q = RedisRequestQueue.__new__(RedisRequestQueue)
    q._publish_stream_event = lambda task, event_type, extra=None: None
    q._is_task_cancelled = Mock(return_value=False)
    q._save_and_respond = Mock(return_value={"status": "ok"})
    q._build_error_response = Mock(return_value={"status": "error"})
    q.redis = Mock()
    app = Mock()
    app.modules = {"base": Mock(), "multimodal": Mock()}
    app.modules["base"]._ = Mock(side_effect=lambda message, lang=None: f"loc:{message}")
    app.config = {"DOCUMENTS_FOLDER": "/tmp/documents"}
    q.app = app

    task = {
        "id": "rlm-img-err",
        "data": {
            "type": "rlm_analysis",
            "text": "What is in the photo?",
            "doc_ids": [],
            "file_data": "BASE64",
            "file_type": "image/jpeg",
            "file_name": "photo.jpg",
        },
        "session_id": "s1",
        "user_id": "u1",
        "lang": "en",
    }
    rm = Mock()
    rm.ensure_vram_for = Mock(return_value=True)
    with patch("app.resource_manager.get_resource_manager", return_value=rm):
        app.modules["multimodal"].describe_image_for_rlm = Mock(return_value=(None, "loc:vision failed"))
        result = q._process_rlm_task(task)

    assert result["status"] == "error"
    q._build_error_response.assert_called_once()
    assert q._build_error_response.call_args[0][1] == "loc:vision failed"


@pytest.mark.unit
def test_process_rlm_task_reports_empty_answer_as_error():
    q = RedisRequestQueue.__new__(RedisRequestQueue)
    q._publish_stream_event = lambda task, event_type, extra=None: None
    q._is_task_cancelled = Mock(return_value=False)
    q._get_model_name = Mock(return_value="reasoning")
    q._save_and_respond = Mock(return_value={"status": "ok"})
    q._build_error_response = Mock(return_value={"status": "error"})
    q.redis = Mock()
    app = Mock()
    base = Mock()
    base._ = Mock(side_effect=lambda message, lang=None: f"loc:{message}")
    app.modules = {"base": base}
    app.config = {"DOCUMENTS_FOLDER": "/tmp/documents"}
    q.app = app

    task = {
        "id": "rlm-empty-1",
        "data": {"type": "rlm_analysis", "text": "q", "doc_ids": ["d1"]},
        "session_id": "s1",
        "user_id": "u1",
        "lang": "en",
    }
    rm = Mock()
    rm.ensure_vram_for_reasoning = Mock(return_value=True)
    with (
        patch("modules.rlm.RlmModule") as mock_rlm_module,
        patch("app.db.get_document", return_value={"file_path": "valery/doc.txt", "filename": "doc.txt"}),
        patch("app.utils.extract_text_from_file", return_value="corpus text"),
        patch("app.resource_manager.get_resource_manager", return_value=rm),
    ):
        mock_rlm_module.return_value.run = Mock(
            return_value=RlmResult(answer="", trace=[RlmTraceStep(1, "python", "{}", "err")], steps=5, error="")
        )
        result = q._process_rlm_task(task)

    assert result["status"] == "error"
    q._build_error_response.assert_called_once()
    assert q._build_error_response.call_args[0][1] == "loc:No response from reasoning model"
    q._save_and_respond.assert_not_called()


@pytest.mark.unit
def test_rlm_task_maps_to_reasoning_model():
    q = RedisRequestQueue.__new__(RedisRequestQueue)
    task = {"type": "rlm_analysis", "data": {"type": "rlm_analysis"}}
    assert q._get_model_for_task(task) == "reasoning"


@pytest.mark.unit
def test_rlm_task_is_classified_slow():
    q = RedisRequestQueue.__new__(RedisRequestQueue)
    assert q._classify_task({"data": {"type": "rlm_analysis"}}) == "slow"


@pytest.mark.unit
def test_resource_manager_rlm_busy_flag():
    rm = ResourceManager.__new__(ResourceManager)
    rm._lock = threading.RLock()
    rm._sd_busy = False
    rm._video_busy = False
    rm._vram_wait_busy = False
    rm._rlm_busy = False
    assert rm.is_gpu_busy() is False
    rm.mark_rlm_busy()
    assert rm.is_gpu_busy() is True
    rm.mark_rlm_idle()
    assert rm.is_gpu_busy() is False


@pytest.mark.unit
def test_process_rlm_task_emits_reading_and_finalizing_stages():
    q = RedisRequestQueue.__new__(RedisRequestQueue)
    events = []
    q._publish_stream_event = lambda task, event_type, extra=None: events.append(extra)
    q._is_task_cancelled = Mock(return_value=False)
    q._get_model_name = Mock(return_value="reasoning")
    q._save_and_respond = Mock(return_value={"status": "ok"})
    q.redis = Mock()
    app = Mock()
    app.modules = {"base": Mock()}
    app.config = {"DOCUMENTS_FOLDER": "/tmp/documents"}
    q.app = app

    task = {
        "id": "rlm-test-1",
        "data": {"type": "rlm_analysis", "text": "q", "doc_ids": ["d1"]},
        "session_id": "s1",
        "user_id": "u1",
        "lang": "en",
    }
    rm = Mock()
    rm.ensure_vram_for_reasoning = Mock(return_value=True)
    with (
        patch("modules.rlm.RlmModule") as mock_rlm_module,
        patch("app.db.get_document", return_value={"file_path": "valery/doc.txt", "filename": "doc.txt"}),
        patch("app.utils.extract_text_from_file") as mock_extract,
        patch("app.resource_manager.get_resource_manager", return_value=rm),
    ):
        mock_extract.side_effect = lambda path: "corpus text" if path == "/tmp/documents/valery/doc.txt" else None
        mock_rlm_module.return_value.run = Mock(return_value=RlmResult(answer="ok", trace=[], steps=1))
        result = q._process_rlm_task(task)

    assert result["status"] == "ok"
    mock_extract.assert_called_with("/tmp/documents/valery/doc.txt")
    stages = [p["stage"] for p in events if p and "stage" in p]
    assert stages[0] == "loading_reasoning_model"
    assert stages.index("rlm_reading") < stages.index("rlm_finalizing")
    # The final answer is a deep-analysis reply: its own model_type drives the
    # microscope emoji in the header while the underlying model is "reasoning".
    assert q._save_and_respond.call_args.kwargs["extra"]["model_type"] == "rlm"


@pytest.mark.unit
def test_process_rlm_task_writes_trace_on_error():
    q = RedisRequestQueue.__new__(RedisRequestQueue)
    q._publish_stream_event = lambda task, event_type, extra=None: None
    q._is_task_cancelled = Mock(return_value=False)
    q._save_and_respond = Mock(return_value={"status": "ok"})
    q._build_error_response = Mock(return_value={"status": "error"})
    q.redis = Mock()
    app = Mock()
    app.modules = {"base": Mock()}
    app.modules["base"]._ = Mock(return_value="localized error")
    app.config = {"DOCUMENTS_FOLDER": "/tmp/documents"}
    q.app = app

    task = {
        "id": "rlm-test-err",
        "data": {"type": "rlm_analysis", "text": "q", "doc_ids": ["d1"]},
        "session_id": "s1",
        "user_id": "u1",
        "lang": "en",
    }
    rm = Mock()
    rm.ensure_vram_for_reasoning = Mock(return_value=True)
    with (
        patch("modules.rlm.RlmModule") as mock_rlm_module,
        patch("app.db.get_document", return_value={"file_path": "valery/doc.txt", "filename": "doc.txt"}),
        patch("app.utils.extract_text_from_file", return_value="corpus text"),
        patch("app.resource_manager.get_resource_manager", return_value=rm),
    ):
        mock_rlm_module.return_value.run = Mock(
            return_value=RlmResult(
                answer="",
                trace=[RlmTraceStep(1, "web_fetch", json.dumps({"query": "q"}), "obs")],
                steps=5,
                error="step limit reached",
            )
        )
        result = q._process_rlm_task(task)

    assert result["status"] == "error"
    key = q.redis.setex.call_args[0][0]
    assert key == "rlm_trace:rlm-test-err"
    assert '"observation": "obs"' in q.redis.setex.call_args[0][2]
