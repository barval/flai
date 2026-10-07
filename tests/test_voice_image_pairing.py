"""Voice + image regression tests.

Incident 2026-10-08: a voice request with an attached image re-queued a plain
text task (no image) because messages.py paired the voice and the image only
when the voice travelled in the legacy "voice" field. With the v12.4
multi-attachment flow the image arrives in the multi "file" parts and the
voice keeps the legacy single slot — the pairing must check both paths.
"""

import base64
import pathlib

MESSAGES = pathlib.Path("app/routes/messages.py").read_text(encoding="utf-8")
QUEUE = pathlib.Path("app/queue.py").read_text(encoding="utf-8")
CHAT_INIT = pathlib.Path("app/static/js/chat-init.js").read_text(encoding="utf-8")

IMG_B64 = base64.b64encode(b"\x89PNG-fake-image-bytes").decode()
AUDIO_B64 = base64.b64encode(b"\x1a\x45\xdf\xa3-fake-audio-bytes").decode()


class TestVoiceImagePairing:
    def test_pairing_checks_both_voice_field_and_legacy_slot(self):
        # voice+image: the voice may be in "voice" (image in legacy slot) OR
        # in the legacy slot (image in multi parts)
        assert "audio_file_data = voice_file_data or file_data" in MESSAGES
        assert "Voice in the legacy slot" in MESSAGES

    def test_transcribe_task_passes_image_list(self):
        # The requeue must carry every image, not only the legacy single slot
        assert 'requeue_request_data["images"]' in QUEUE
        assert 'request_data["images"]' in MESSAGES

    def test_js_captures_multi_queue_image_for_rlm(self):
        # rlmAwaitingVoiceImage must also come from attachedFiles, not only
        # from the legacy single slot
        assert "rlmAwaitingVoiceImage" in CHAT_INIT
        assert "tempFiles" in CHAT_INIT or "attachedFiles" in CHAT_INIT


class TestProcessTranscribeUnit:
    """Unit test over _process_transcribe_task requeue shape."""

    def _make_queue(self, monkeypatch, request_data):
        import app.queue as queue_mod

        q = queue_mod.RedisRequestQueue.__new__(queue_mod.RedisRequestQueue)
        q.app = type("A", (), {})()
        q.app.logger = type("L", (), {"info": staticmethod(lambda *a, **k: None)})()
        q.app.modules = {
            "audio": type("M", (), {"transcribe": staticmethod(lambda *a, **k: "text")})(),
            "base": type(
                "B",
                (),
                {
                    "_": staticmethod(lambda s, loc="ru", lang="ru": s),
                    "Transcribed": "Transcribed",
                },
            )(),
        }
        q.app.request_queue = q

        captured = {}

        def fake_add(user_id, session_id, data, user_class, lang="ru", task_type=None):
            captured["data"] = data
            return "new-id", {"position": 1}

        monkeypatch.setattr(q, "add_request", fake_add)

        save_calls = {}

        def fake_save(session_id, role, content, *a, **k):
            save_calls["content"] = content
            return 1

        monkeypatch.setattr(queue_mod, "save_message", fake_save)

        from flask_babel import force_locale  # noqa: F401 — imported in handler

        class FakeCtx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(
            queue_mod,
            "force_locale",
            lambda loc: FakeCtx(),
        )
        monkeypatch.setattr(queue_mod, "get_current_time_in_timezone_for_db", lambda app: "t")

        result = q._process_transcribe_task({"user_id": "u1", "session_id": "s1", "data": request_data, "lang": "ru"})
        return captured, result

    def test_voice_image_requeue_is_image_task(self, monkeypatch):
        img2 = base64.b64encode(b"second-image-bytes").decode()
        captured, result = self._make_queue(
            monkeypatch,
            {
                "type": "transcribe_audio",
                "file_data": AUDIO_B64,
                "file_type": "audio/webm",
                "file_name": "voice.webm",
                "voice_record": True,
                "image_data": IMG_B64,
                "image_type": "image/jpeg",
                "image_name": "img.jpg",
                "images": [IMG_B64, img2],
            },
        )
        assert captured["data"]["type"] == "image"
        assert captured["data"]["file_data"] == IMG_B64
        # The primary image rides in file_data; the extra one must survive
        assert captured["data"]["images"] == [IMG_B64, img2]
        assert result["request_id"] == "new-id"

    def test_voice_single_image_requeue_has_no_images_key(self, monkeypatch):
        captured, _result = self._make_queue(
            monkeypatch,
            {
                "type": "transcribe_audio",
                "file_data": AUDIO_B64,
                "file_type": "audio/webm",
                "file_name": "voice.webm",
                "voice_record": True,
                "image_data": IMG_B64,
                "image_type": "image/jpeg",
                "image_name": "img.jpg",
            },
        )
        assert captured["data"]["type"] == "image"
        assert captured["data"]["file_data"] == IMG_B64
        assert "images" not in captured["data"]

    def test_voice_only_requeue_is_text_task(self, monkeypatch):
        captured, result = self._make_queue(
            monkeypatch,
            {
                "type": "transcribe_audio",
                "file_data": AUDIO_B64,
                "file_type": "audio/webm",
                "file_name": "voice.webm",
                "voice_record": True,
            },
        )
        assert captured["data"]["type"] == "text"
        assert "images" not in captured["data"]
        assert result["request_id"] == "new-id"
