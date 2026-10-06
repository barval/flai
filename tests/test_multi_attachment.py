"""Multi-image chat support: chat_with_images() in llamacpp_client and the
multimodal multi-image helpers with the per-image fallback path."""

import base64
import io
import json as _json
import logging
import os
import pathlib

PNG_1PX = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"0" * 40  # not a real PNG; tests mock the network
).decode("ascii")
PNG_1PX_RAW = b"\x89PNG\r\n\x1a\n" + b"0" * 40


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


class TestMultiAttachmentPersistence:
    """send_message persists every extra image to disk and stores file_path
    in the content JSON; get_session_messages keeps base64 only for parts
    without a stored file."""

    def _login(self, client):
        with client.application.app_context():
            from app.userdb import create_user, get_user_by_login

            if not get_user_by_login("multiatt"):
                create_user("multiatt", "pass123", "Multi Attachment")

        client.post("/login", data={"login": "multiatt", "password": "pass123"})
        resp = client.post("/api/sessions/new")
        self._session_id = resp.get_json()["id"]

    def _png_file(self, name="img.png"):
        import io

        return (io.BytesIO(PNG_1PX_RAW), name, "image/png")

    def test_send_message_saves_every_image_to_disk(self, client, test_app):
        self._login(client)
        response = client.post(
            "/api/send_message",
            data={"message": "compare", "file": [self._png_file("a.png"), self._png_file("b.png")]},
            content_type="multipart/form-data",
        )
        assert response.status_code == 200, response.get_json()
        upload_folder = test_app.config["UPLOAD_FOLDER"]
        saved = list(pathlib.Path(upload_folder).rglob("*.png"))
        assert len(saved) == 2, f"expected both images on disk, found {saved}"

    def test_content_json_keeps_file_paths(self, client, test_app):
        self._login(client)
        response = client.post(
            "/api/send_message",
            data={"message": "compare", "file": [self._png_file("a.png"), self._png_file("b.png")]},
            content_type="multipart/form-data",
        )
        assert response.status_code == 200
        with test_app.app_context():
            from app import db as dbmod

            session_id = self._session_id
            msgs = dbmod.get_session_messages(session_id)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert user_msgs, "user message missing from history"
        parts = _json.loads(user_msgs[-1]["content"])
        image_parts = [p for p in parts if p.get("type") == "image"]
        assert len(image_parts) == 2
        for p in image_parts:
            assert p.get("file_path"), "every image part must carry file_path"
        full = os.path.join(test_app.config["UPLOAD_FOLDER"], image_parts[0]["file_path"])
        assert os.path.exists(full)

    def test_history_strips_file_data_only_for_parts_with_file_path(self, test_app):
        with test_app.app_context():
            from app.db import create_session, get_session_messages, save_message

            username = "multiatt-history"
            session_id = create_session(username, title="strip test")
            content = _json.dumps(
                [
                    {"type": "text", "text": "q"},
                    {
                        "type": "image",
                        "file_data": "AAAA",
                        "file_type": "image/png",
                        "file_name": "a.png",
                        "file_path": "sess/a.png",
                    },
                    {
                        "type": "image",
                        "file_data": "BBBB",
                        "file_type": "image/png",
                        "file_name": "b.png",
                        "file_path": None,
                    },
                ]
            )
            save_message(session_id, "user", content, file_path="sess/a.png")
            msgs = get_session_messages(session_id)
            parts = _json.loads(msgs[0]["content"])
            images = [p for p in parts if p.get("type") == "image"]
            assert images[0]["file_data"] is None, "part with file_path must be stripped"
            assert images[0]["file_path"] == "sess/a.png"
            assert images[1]["file_data"] == "BBBB", "part without file_path keeps base64"

    def test_history_falls_back_to_row_path_for_first_pathless_part(self, test_app):
        """Legacy rows store the first image in BOTH the row file_path and the
        content part (without a part-level path). The reader must substitute
        the row path for that first action part so its base64 is stripped."""
        with test_app.app_context():
            from app.db import create_session, get_session_messages, save_message

            username = "multiatt-legacy"
            session_id = create_session(username, title="legacy strip test")
            content = _json.dumps(
                [
                    {"type": "text", "text": "q"},
                    {
                        "type": "image",
                        "file_data": "AAAA",
                        "file_type": "image/png",
                        "file_name": "a.png",
                    },
                    {
                        "type": "image",
                        "file_data": "BBBB",
                        "file_type": "image/png",
                        "file_name": "b.png",
                    },
                ]
            )
            save_message(session_id, "user", content, file_path="sess/a.png")
            msgs = get_session_messages(session_id)
            parts = _json.loads(msgs[0]["content"])
            images = [p for p in parts if p.get("type") == "image"]
            assert images[0]["file_path"] == "sess/a.png", "first pathless part must reuse the row path"
            assert images[0]["file_data"] is None
            assert images[1]["file_data"] == "BBBB", "non-first parts keep base64 (no path on disk yet)"

    def test_send_message_audio_part_gets_disk_path(self, client, test_app):
        """An uploaded audio file must be saved like images: part file_path on
        disk, no base64 left in the content JSON."""
        self._login(client)
        wav = (
            b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
            + b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        response = client.post(
            "/api/send_message",
            data={
                "message": "transcribe this",
                "file": (io.BytesIO(wav), "note.wav", "audio/wav"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 200, response.get_json()
        with test_app.app_context():
            from app import db as dbmod

            msgs = dbmod.get_session_messages(self._session_id)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert user_msgs
        parts = _json.loads(user_msgs[-1]["content"])
        audio_parts = [p for p in parts if p.get("type") == "audio"]
        assert len(audio_parts) == 1
        assert audio_parts[0].get("file_path"), "audio part must carry a disk path"
        assert "file_data" not in audio_parts[0], "audio base64 must not reach history"
        full = os.path.join(test_app.config["UPLOAD_FOLDER"], audio_parts[0]["file_path"])
        assert os.path.exists(full)

    def test_send_message_document_part_gets_disk_path(self, client, test_app):
        """A chat-attached document must be saved: part file_path on disk, no
        base64 left in the content JSON."""
        self._login(client)
        pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
        response = client.post(
            "/api/send_message",
            data={
                "message": "summarize it",
                "file": (io.BytesIO(pdf), "report.pdf", "application/pdf"),
            },
            content_type="multipart/form-data",
        )
        assert response.status_code == 200, response.get_json()
        with test_app.app_context():
            from app import db as dbmod

            msgs = dbmod.get_session_messages(self._session_id)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert user_msgs
        parts = _json.loads(user_msgs[-1]["content"])
        file_parts = [p for p in parts if p.get("type") == "file"]
        assert len(file_parts) == 1
        assert file_parts[0].get("file_path"), "document part must carry a disk path"
        assert "file_data" not in file_parts[0], "document base64 must not reach history"
        full = os.path.join(test_app.config["UPLOAD_FOLDER"], file_parts[0]["file_path"])
        assert os.path.exists(full)


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
