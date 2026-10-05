"""Multi-image chat support: chat_with_images() in llamacpp_client and the
multimodal multi-image helpers with the per-image fallback path."""

import base64
import logging

PNG_1PX = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"0" * 40  # not a real PNG; tests mock the network
).decode("ascii")


class TestChatWithImages:
    """app/llamacpp_client.py: chat_with_images() builds N image_url parts."""

    def _client(self, monkeypatch):
        from app.llamacpp_client import LlamaCppClient

        client = LlamaCppClient.__new__(LlamaCppClient)
        captured = {}

        def fake_chat(messages, model_type="multimodal", lang="ru", **kwargs):
            captured["messages"] = messages
            captured["model_type"] = model_type
            return "ok"

        monkeypatch.setattr(client, "chat", fake_chat)
        return client, captured

    def test_single_image_same_shape_as_chat_with_image(self, monkeypatch):
        client, captured = self._client(monkeypatch)
        out = client.chat_with_images("hello", [PNG_1PX])
        assert out == "ok"
        content = captured["messages"][0]["content"]
        assert captured["messages"][0]["role"] == "user"
        kinds = [p["type"] for p in content]
        assert kinds == ["text", "image_url"]
        assert content[0]["text"] == "hello"
        url = content[1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")

    def test_multiple_images_in_one_message(self, monkeypatch):
        client, captured = self._client(monkeypatch)
        images = [PNG_1PX, PNG_1PX, PNG_1PX]
        client.chat_with_images("compare", images)
        content = captured["messages"][0]["content"]
        kinds = [p["type"] for p in content]
        assert kinds == ["text", "image_url", "image_url", "image_url"]

    def test_data_url_prefix_added_once(self, monkeypatch):
        client, captured = self._client(monkeypatch)
        client.chat_with_images("x", [f"data:image/png;base64,{PNG_1PX}"])
        url = captured["messages"][0]["content"][1]["image_url"]["url"]
        assert url == f"data:image/png;base64,{PNG_1PX}"

    def test_stream_variant_yields(self, monkeypatch):
        from app.llamacpp_client import LlamaCppClient

        client = LlamaCppClient.__new__(LlamaCppClient)

        def fake_stream(messages, model_type="multimodal", lang="ru", **kwargs):
            yield "a"
            yield "b"

        monkeypatch.setattr(client, "chat_stream", fake_stream)
        out = list(client.chat_with_images_stream("x", [PNG_1PX, PNG_1PX]))
        assert out == ["a", "b"]


class TestProcessImagesWithText:
    """modules/multimodal.py: process_images_with_text() — N images + fallback."""

    def _module(self, monkeypatch):
        from modules.multimodal import MultimodalModule

        mod = MultimodalModule.__new__(MultimodalModule)
        mod.logger = __import__("logging").getLogger("test")

        class _App:
            config = {}

        mod.app = _App()

        captured = {}

        def fake_chat_with_images(text, images, model_type="multimodal", lang="ru"):
            captured["images"] = images
            captured["text"] = text
            return "multi answer"

        mod.llamacpp = type(
            "L",
            (),
            {
                "chat_with_images": staticmethod(fake_chat_with_images),
                "chat_with_image": staticmethod(
                    lambda text, image_base64, model_type="multimodal", lang="ru": "single answer"
                ),
            },
        )()
        monkeypatch.setattr(mod, "check_availability", lambda: True)
        monkeypatch.setattr(mod, "_prepare_image_prompt", lambda *a, **k: "prompt")
        monkeypatch.setattr(mod, "_ensure_llamacpp_compatible", lambda data: (data, False))
        return mod, captured

    def test_multi_call_first(self, monkeypatch):
        mod, captured = self._module(monkeypatch)
        answer, error = mod.process_images_with_text(["img1", "img2"], "q", "now")
        assert error is None
        assert answer == "multi answer"
        assert captured["images"] == ["img1", "img2"]

    def test_vram_error_returns_error(self, monkeypatch):
        mod, _ = self._module(monkeypatch)

        def fail(*a, **k):
            return "CUDA out of memory"

        mod.llamacpp.chat_with_images = staticmethod(fail)
        monkeypatch.setattr(mod, "_is_vram_error", lambda r: "out of memory" in r)
        answer, error = mod.process_images_with_text(["img1"], "q", "now")
        assert answer is None
        assert error is not None
        assert "GPU" in error or "memory" in error

    def test_empty_list_rejected(self, monkeypatch):
        mod, _ = self._module(monkeypatch)
        answer, error = mod.process_images_with_text([], "q", "now")
        assert answer is None
        assert error is not None

    def test_describe_each_image_sequential(self, monkeypatch):
        mod, _ = self._module(monkeypatch)
        calls = []

        def fake_describe(data, lang="ru"):
            calls.append(data)
            return f"description of {data}", None

        monkeypatch.setattr(mod, "describe_image_for_rlm", fake_describe)
        result = mod.describe_images_for_context(["a", "b", "c"], lang="ru")
        assert calls == ["a", "b", "c"]
        assert result == [
            ("a", "description of a", None),
            ("b", "description of b", None),
            ("c", "description of c", None),
        ]

    def test_describe_each_image_collects_errors(self, monkeypatch):
        mod, _ = self._module(monkeypatch)

        def fake_describe(data, lang="ru"):
            if data == "b":
                return None, "boom"
            return f"description of {data}", None

        monkeypatch.setattr(mod, "describe_image_for_rlm", fake_describe)
        result = mod.describe_images_for_context(["a", "b"], lang="ru")
        assert result == [("a", "description of a", None), ("b", None, "boom")]


class TestImageChatTaskMulti:
    """app/queue.py: _process_image_chat_task accepts a list of images."""

    def _queue(self, monkeypatch):
        from app.queue import RedisRequestQueue

        q = RedisRequestQueue.__new__(RedisRequestQueue)

        class _App:
            class modules:  # noqa: N801
                pass

        class _MM:
            available = True
            logger = __import__("logging").getLogger("test")

            def validate_image(self, *a, **k):
                return True, None

            def process_images_with_text(self, images, text, ts, lang="ru", **k):
                return "multi answer", None

            def process_image_with_text(self, *a, **k):
                return "single answer", None

            def generate_edit_params(self, *a, **k):
                return None, None

        _App.modules = {"multimodal": _MM(), "base": type("B", (), {"_": staticmethod(lambda s, _l: s)})}
        q.app = _App()
        q._save_and_respond = lambda *a, **k: {"response": a[1] if len(a) > 1 else k.get("response")}
        q._get_model_name = lambda *a: "test-model"
        return q

    def test_list_of_images_routes_to_multi(self, monkeypatch):
        q = self._queue(monkeypatch)
        result = q._process_image_chat_task(
            ["img1", "img2"], "image/jpeg", "a.jpg", "question", "s1", "now", "ru", "user1"
        )
        assert result["response"] == "multi answer"

    def test_single_image_string_still_works(self, monkeypatch):
        q = self._queue(monkeypatch)
        result = q._process_image_chat_task("img1", "image/jpeg", "a.jpg", "question", "s1", "now", "ru", "user1")
        assert result["response"] == "single answer"


class TestDocChatTask:
    """app/queue.py: doc_chat — save document, index it, requeue the question."""

    def _queue(self, monkeypatch):
        from app.queue import RedisRequestQueue

        q = RedisRequestQueue.__new__(RedisRequestQueue)
        logging.getLogger("test")

        class _App:
            config = {"UPLOAD_FOLDER": "/tmp/uploads", "DOCUMENTS_FOLDER": "/tmp/documents"}
            logger = logging.getLogger("test")
            modules = {}

        q.app = _App()
        q._publish_document_event = lambda *a: None
        q.add_request = lambda *a, **k: ("req-1", {"position": 1, "queue_type": "fast", "estimated_seconds": 5})
        captured = {}
        monkeypatch.setattr(
            "app.queue._run_document_indexing",
            lambda app, doc_id, file_path, user_id, indexing_started_at, publish: (True, "ok", "m", True),
        )
        import app.queue as qmod

        def fake_save_document(user_id, doc_id, filename, file_size, file_ext, file_path):
            captured["doc"] = (user_id, doc_id, filename)

        def fake_update_status(doc_id, status, **k):
            captured.setdefault("status", []).append((doc_id, status))

        monkeypatch.setattr(qmod, "save_document", fake_save_document)
        monkeypatch.setattr(qmod, "update_document_index_status", fake_update_status)
        return q, captured

    def test_doc_chat_requeues_text_with_doc_ids(self, monkeypatch, tmp_path):
        q, captured = self._queue(monkeypatch)
        # Point DOCUMENTS_FOLDER into tmp_path
        user_folder = tmp_path / "docs" / "alice"
        user_folder.mkdir(parents=True)
        q.app.config["DOCUMENTS_FOLDER"] = str(tmp_path / "docs")

        task = {
            "id": "t1",
            "user_id": "alice",
            "session_id": "s1",
            "lang": "ru",
            "user_class": 2,
            "data": {
                "type": "doc_chat",
                "text": "Что в документе?",
                "doc_file_data": base64.b64encode(b"%PDF-tiny").decode(),
                "doc_file_type": "application/pdf",
                "doc_file_name": "contract.pdf",
            },
        }
        result = q._process_doc_chat_task(task)
        assert result["status"] == "requeued"
        # document saved under the user's folder
        assert captured["doc"][0] == "alice"
        assert captured["doc"][2] == "contract.pdf"
        # the requeued request carries doc_ids
        assert q.last_request_data["doc_ids"], "doc_ids must be set on requeue"
        assert q.last_request_data["type"] == "text"

    def test_doc_chat_index_failure_reports_error(self, monkeypatch, tmp_path):
        q, captured = self._queue(monkeypatch)
        user_folder = tmp_path / "docs" / "alice"
        user_folder.mkdir(parents=True)
        q.app.config["DOCUMENTS_FOLDER"] = str(tmp_path / "docs")

        import app.queue as qmod

        monkeypatch.setattr(
            qmod,
            "_run_document_indexing",
            lambda app, doc_id, file_path, user_id, indexing_started_at, publish: (False, "Indexing failed", "", False),
        )

        task = {
            "id": "t2",
            "user_id": "alice",
            "session_id": "s1",
            "lang": "ru",
            "user_class": 2,
            "data": {
                "type": "doc_chat",
                "text": "q",
                "doc_file_data": base64.b64encode(b"%PDF-tiny").decode(),
                "doc_file_type": "application/pdf",
                "doc_file_name": "bad.pdf",
            },
        }
        q._build_error_response = lambda *a, **k: {"is_error": True, "response": a[1] if len(a) > 1 else None}
        result = q._process_doc_chat_task(task)
        assert result.get("is_error") is True


class TestImageGenExamples:
    """_requeue_image_task carries attached images into image_gen."""

    def test_requeue_image_task_carries_images(self, monkeypatch):
        from app.queue import RedisRequestQueue

        q = RedisRequestQueue.__new__(RedisRequestQueue)

        class _Base:
            @staticmethod
            def _(s, lang=None):
                return s

        q.app = type("A", (), {"modules": {"base": _Base()}, "logger": logging.getLogger("t")})()
        captured = {}
        q.add_request = lambda user_id, sid, data, uc, lang=None: (
            captured.update(data) or ("req-1", {"position": 1, "queue_type": "slow", "estimated_seconds": 5})
        )

        import app.queue as qmod

        monkeypatch.setattr(qmod, "current_usage_account_id", lambda: None)
        monkeypatch.setattr(qmod, "finish_usage_account", lambda: None)
        q._requeue_image_task("draw like this", "s1", "u1", "ru", images=["img1", "img2"])
        assert captured["images"] == ["img1", "img2"]
        assert captured["type"] == "image_gen"
