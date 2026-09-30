"""OpenAI audio endpoints: `POST /v1/audio/speech` and `/v1/audio/transcriptions`.

Speech is a direct call to the configured TTS service (no queue, no GPU, no
session). Transcription is queued like any other model call and returns text
only — FLAI's Whisper wrapper has no timestamps, so `srt`/`vtt` are refused
rather than faked.
"""

import base64
import inspect
import io
import json
from unittest.mock import Mock

import pytest

from app.api_tokens import create_api_token
from app.userdb import create_user

SPEECH = "/v1/audio/speech"
TRANSCRIPTIONS = "/v1/audio/transcriptions"
WAV = b"RIFF....WAVEfmt "


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


def tts_module(audio=WAV, mime="audio/wav", backend="kokoro"):
    module = Mock()
    module.available = True
    module.backend = backend
    module.synthesize.return_value = (audio, mime)
    return module


def install_tts(test_app, module):
    test_app.modules["tts"] = module
    return module


def post_speech(client, token, **payload):
    return client.post(SPEECH, json=payload, headers=bearer(token))


def post_audio(client, token, content=WAV, filename="clip.wav", content_type="audio/wav", **form):
    data = {"file": (io.BytesIO(content), filename, content_type)}
    data.update(form)
    return client.post(TRANSCRIPTIONS, data=data, headers=bearer(token), content_type="multipart/form-data")


@pytest.fixture
def transcribe_stub(test_app, monkeypatch):
    """Stub the bridge and the new queue task; capture what was enqueued."""
    from unittest.mock import Mock

    captured = {}

    def _enqueue(api_user_arg, file_data, file_type, file_name, language=None):
        captured.update(
            {
                "login": api_user_arg["login"],
                "language": language,
                "file_data": file_data,
                "file_type": file_type,
                "file_name": file_name,
            }
        )
        return "task-tr-1"

    audio = Mock()
    audio.available = True

    def _is_audio_file(mime, fname):
        return mime and mime.startswith("audio/")

    audio.is_audio_file.side_effect = _is_audio_file
    test_app.modules["audio"] = audio

    monkeypatch.setattr("app.routes.api_v1.enqueue_transcription", _enqueue)
    monkeypatch.setattr(
        "app.routes.api_v1.wait_for_result",
        lambda login, task_id, timeout_s: {"status": "completed", "text": "hello there"},
    )
    return captured


@pytest.mark.unit
class TestSpeechValidation:
    def test_missing_input_is_rejected(self, client, api_user):
        response = post_speech(client, api_user, voice="alloy")
        assert response.status_code == 400
        assert response.get_json()["error"]["type"] == "invalid_request_error"

    def test_empty_input_is_rejected(self, client, api_user):
        assert post_speech(client, api_user, input="   ").status_code == 400

    def test_non_string_input_is_rejected(self, client, api_user):
        assert post_speech(client, api_user, input=["a", "b"]).status_code == 400

    def test_body_must_be_a_json_object(self, client, api_user):
        response = client.post(SPEECH, data="[]", headers=bearer(api_user), content_type="application/json")
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_body"

    def test_unauthenticated_request_is_401(self, client):
        assert client.post(SPEECH, json={"input": "hi"}).status_code == 401

    def test_unknown_response_format_is_rejected(self, client, api_user, test_app):
        install_tts(test_app, tts_module(backend="piper", mime="audio/mpeg"))
        response = post_speech(client, api_user, input="hi", response_format="ogg")
        assert response.status_code == 400
        error = response.get_json()["error"]
        assert error["code"] == "invalid_response_format"
        assert "mp3" in error["message"]

    def test_non_boolean_stream_style_field_is_ignored(self, client, api_user, test_app):
        """`speed` is part of the OpenAI shape; FLAI cannot apply it."""
        module = install_tts(test_app, tts_module())
        response = post_speech(client, api_user, input="hi", speed=1.5, model="tts-1")
        assert response.status_code == 200
        assert "speed" not in (module.synthesize.call_args.kwargs or {})


@pytest.mark.unit
class TestSpeechSynthesis:
    def test_returns_audio_with_the_real_mime_type(self, client, api_user, test_app):
        install_tts(test_app, tts_module(mime="audio/x-wav"))
        response = post_speech(client, api_user, input="hello")
        assert response.status_code == 200
        assert response.data == WAV
        assert response.headers["Content-Type"].startswith("audio/x-wav")

    def test_piper_default_returns_mp3_with_matching_extension(self, client, api_user, test_app):
        install_tts(test_app, tts_module(mime="audio/mpeg", backend="piper"))
        response = post_speech(client, api_user, input="hello")
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("audio/mpeg")
        assert response.headers["Content-Disposition"] == 'attachment; filename="speech.mp3"'

    def test_matching_mp3_format_is_supported_for_piper(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module(mime="audio/mpeg", backend="piper"))
        response = post_speech(client, api_user, input="hello", response_format="mp3")
        assert response.status_code == 200
        module.synthesize.assert_called_once()

    def test_incompatible_format_is_rejected_before_synthesis(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module(mime="audio/mpeg", backend="piper"))
        response = post_speech(client, api_user, input="hello", response_format="wav")
        assert response.status_code == 400
        assert module.synthesize.call_count == 0

    def test_unexpected_backend_mime_is_not_returned_as_success(self, client, api_user, test_app):
        install_tts(test_app, tts_module(mime="audio/ogg"))
        response = post_speech(client, api_user, input="hello")
        assert response.status_code == 500
        assert response.get_json()["error"]["code"] == "synthesis_failed"

    def test_filename_follows_the_mime_type(self, client, api_user, test_app):
        """A mislabelled extension is worse than none: derive it from the real type."""
        install_tts(test_app, tts_module(mime="audio/mpeg", backend="piper"))
        response = post_speech(client, api_user, input="hello")
        assert response.headers["Content-Disposition"] == 'attachment; filename="speech.mp3"'

    def test_uses_the_api_users_language_and_gender(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module())
        post_speech(client, api_user, input="hello")
        args, kwargs = module.synthesize.call_args
        assert args[0] == "hello"
        assert args[1] == "ru"
        assert args[2] == "male"

    def test_voice_is_forwarded(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module())
        post_speech(client, api_user, input="hello", voice="marina")
        assert module.synthesize.call_args.kwargs["voice"] == "marina"

    def test_language_and_gender_extensions_override_the_account(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module())
        post_speech(client, api_user, input="hello", language="en", gender="female")
        args, _ = module.synthesize.call_args
        assert (args[1], args[2]) == ("en", "female")

    def test_unsupported_language_is_rejected(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module())
        response = post_speech(client, api_user, input="hi", language="xx")
        assert response.status_code == 400
        assert module.synthesize.call_count == 0

    def test_unsupported_gender_is_rejected(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module())
        response = post_speech(client, api_user, input="hi", gender="other")
        assert response.status_code == 400
        assert module.synthesize.call_count == 0

    def test_non_string_voice_is_rejected(self, client, api_user, test_app):
        install_tts(test_app, tts_module())
        assert post_speech(client, api_user, input="hi", voice=7).status_code == 400

    def test_tts_module_missing_is_503(self, client, api_user, test_app):
        test_app.modules.pop("tts", None)
        response = post_speech(client, api_user, input="hi")
        assert response.status_code == 503
        assert response.get_json()["error"]["type"] == "service_unavailable"

    def test_unavailable_tts_service_is_503(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module())
        module.available = False
        assert post_speech(client, api_user, input="hi").status_code == 503

    def test_failed_synthesis_is_500(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module())
        module.synthesize.return_value = (None, None)
        response = post_speech(client, api_user, input="hi")
        assert response.status_code == 500
        assert response.get_json()["error"]["message"].startswith("⚠️ ")

    def test_synthesis_exception_is_not_exposed(self, client, api_user, test_app):
        module = install_tts(test_app, tts_module())
        module.synthesize.side_effect = RuntimeError("private backend path")
        response = post_speech(client, api_user, input="hi")
        assert response.status_code == 500
        message = response.get_json()["error"]["message"]
        assert message.startswith("⚠️ ")
        assert "private backend path" not in message

    def test_error_message_is_localized_for_a_russian_owner(self, client, test_app):
        create_user(login="ruuser", password="pw-ruuser-123456", name="RU", language="ru")
        token, _ = create_api_token("ruuser", name="test")
        test_app.modules.pop("tts", None)
        response = post_speech(client, token, input="hi")
        message = response.get_json()["error"]["message"]
        assert message.startswith("⚠️ ")
        assert any("Ѐ" <= ch <= "ӿ" for ch in message)

    def test_api_error_language_comes_from_the_key_owner(self, client, test_app):
        create_user(login="enuser", password="pw-enuser-123456", name="EN", language="en")
        token, _ = create_api_token("enuser", name="test")
        response = post_speech(client, token, input=" ")
        message = response.get_json()["error"]["message"]
        assert message.startswith("⚠️ ")
        assert "non-empty string" in message
        assert not any("Ѐ" <= ch <= "ӿ" for ch in message)

    def test_speech_does_not_persist_a_session(self, client, api_user, test_app, monkeypatch):
        saved = []
        monkeypatch.setattr("app.db.save_message", lambda *a, **k: saved.append(a))
        install_tts(test_app, tts_module())
        post_speech(client, api_user, input="hello")
        assert saved == []

    def test_speech_takes_no_gpu_lock_and_no_queue_slot(self, client, api_user, test_app, monkeypatch):
        """TTS is an HTTP call to Piper/Kokoro: it must not touch the queue."""
        from app.routes import api_v1

        enqueued = []
        monkeypatch.setattr(
            test_app.request_queue,
            "add_request",
            lambda *a, **k: enqueued.append(a) or ("task", {}),
        )
        monkeypatch.setattr(api_v1, "_acquire_wait_slot", lambda: pytest.fail("speech must not take a wait slot"))
        install_tts(test_app, tts_module())
        assert post_speech(client, api_user, input="hello").status_code == 200
        assert enqueued == []


@pytest.mark.unit
class TestTranscriptionValidation:
    def test_missing_file_is_rejected(self, client, api_user):
        response = client.post(TRANSCRIPTIONS, data={}, headers=bearer(api_user))
        assert response.status_code == 400
        assert response.get_json()["error"]["type"] == "invalid_request_error"

    def test_unsupported_media_type_is_rejected(self, client, api_user, test_app, transcribe_stub):
        response = post_audio(client, api_user, filename="notes.txt", content_type="text/plain")
        assert response.status_code == 400
        assert transcribe_stub == {}

    def test_srt_and_vtt_are_refused(self, client, api_user, transcribe_stub):
        """FLAI's Whisper wrapper returns plain text with no timestamps."""
        for fmt in ("srt", "vtt"):
            response = post_audio(client, api_user, response_format=fmt)
            assert response.status_code == 400
            assert response.get_json()["error"]["code"] == "invalid_response_format"
        assert transcribe_stub == {}

    def test_unknown_response_format_is_refused(self, client, api_user, transcribe_stub):
        assert post_audio(client, api_user, response_format="verbose_json").status_code == 400

    def test_empty_upload_is_rejected(self, client, api_user, transcribe_stub):
        assert post_audio(client, api_user, content=b"").status_code == 400
        assert transcribe_stub == {}

    def test_unsupported_language_is_rejected_before_enqueue(self, client, api_user, transcribe_stub):
        response = post_audio(client, api_user, language="xx")
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_language"
        assert transcribe_stub == {}

    def test_upload_over_configured_limit_uses_openai_error_envelope(self, test_app, api_user, monkeypatch):
        test_app.config["MAX_CONTENT_LENGTH"] = 128
        enqueue = []
        monkeypatch.setattr("app.routes.api_v1.enqueue_transcription", lambda *a, **k: enqueue.append(a))
        response = post_audio(test_app.test_client(), api_user, content=b"x" * 1024)
        assert response.status_code == 413
        assert response.get_json()["error"]["code"] == "request_too_large"
        assert response.get_json()["error"]["message"].startswith("⚠️ ")
        assert enqueue == []

    def test_upload_limit_handler_uses_exact_v1_boundary(self, test_app):
        from werkzeug.exceptions import RequestEntityTooLarge

        from app.routes.api_v1 import api_payload_too_large

        with test_app.test_request_context("/v1evil/large"):
            response = api_payload_too_large(RequestEntityTooLarge())
        assert response.mimetype == "text/html"

    def test_audio_module_missing_is_503(self, client, api_user, test_app, transcribe_stub):
        test_app.modules.pop("audio", None)
        response = post_audio(client, api_user)
        assert response.status_code == 503
        assert transcribe_stub == {}


@pytest.mark.unit
class TestTranscriptionEnqueue:
    @pytest.fixture(autouse=True)
    def _install_audio(self, test_app):
        from unittest.mock import Mock

        audio = Mock()
        audio.available = True
        audio.is_audio_file.return_value = True
        test_app.modules["audio"] = audio

    def test_json_returns_the_transcript(self, client, api_user, transcribe_stub):
        response = post_audio(client, api_user)
        assert response.status_code == 200
        assert response.get_json() == {"text": "hello there"}

    def test_text_returns_plain_text(self, client, api_user, transcribe_stub):
        response = post_audio(client, api_user, response_format="text")
        assert response.status_code == 200
        assert response.data == b"hello there"
        assert response.headers["Content-Type"].startswith("text/plain")

    def test_payload_carries_base64_data_mime_and_filename(self, client, api_user, transcribe_stub):
        post_audio(client, api_user, content=WAV, filename="clip.webm", content_type="audio/webm")
        assert base64.b64decode(transcribe_stub["file_data"]) == WAV
        assert transcribe_stub["file_type"] == "audio/webm"
        assert transcribe_stub["file_name"] == "clip.webm"

    def test_enqueues_under_the_api_identity_without_a_session(self, client, api_user, transcribe_stub):
        post_audio(client, api_user)
        assert transcribe_stub["login"] == "apiuser"
        assert transcribe_stub["language"] == "ru"
        assert "session" not in transcribe_stub

    def test_model_field_is_ignored(self, client, api_user, transcribe_stub):
        assert post_audio(client, api_user, model="whisper-1").status_code == 200

    def test_language_extension_is_forwarded(self, client, api_user, transcribe_stub, test_app, monkeypatch):
        captured = {}

        def _enqueue(api_user_arg, file_data, file_type, file_name, language=None):
            captured["language"] = language

            return "task-tr-1"

        monkeypatch.setattr("app.routes.api_v1.enqueue_transcription", _enqueue)
        post_audio(client, api_user, language="en")
        assert captured["language"] == "en"

    def test_failure_is_reported_as_502(self, client, api_user, monkeypatch):
        from app.api_bridge import ApiTaskError

        monkeypatch.setattr("app.routes.api_v1.enqueue_transcription", lambda *a, **k: "task-1")

        def _fail(*args, **kwargs):
            raise ApiTaskError("Failed to recognize speech")

        monkeypatch.setattr("app.routes.api_v1.wait_for_result", _fail)
        response = post_audio(client, api_user)
        assert response.status_code == 502
        assert response.get_json()["error"]["message"].startswith("⚠️ ")


@pytest.mark.unit
class TestTranscriptionQueueTask:
    """The dedicated API task type: no session, no chat message, no usage account."""

    @staticmethod
    def _task(**data):
        return {
            "id": "task-1",
            "user_id": "apiuser",
            "session_id": "",
            "lang": "ru",
            "data": {"type": "api_transcribe", **data},
        }

    @staticmethod
    def _queue(app, audio):
        from app.queue import RedisRequestQueue

        q = RedisRequestQueue.__new__(RedisRequestQueue)
        q.app = app
        q.logger = Mock()
        return q

    def _app(self, audio):
        app = Mock()
        app.modules = {"base": Mock(**{"_": lambda text, lang=None: text}), "audio": audio}
        return app

    def test_is_classified_fast(self):
        from app.queue import RedisRequestQueue

        q = RedisRequestQueue.__new__(RedisRequestQueue)
        assert q._classify_task({"data": {"type": "api_transcribe"}}) == "fast"

    def test_does_not_reserve_a_llm_model(self):
        from app.queue import RedisRequestQueue

        q = RedisRequestQueue.__new__(RedisRequestQueue)
        assert q._get_model_for_task({"data": {"type": "api_transcribe", "file_type": "audio/wav"}}) == "none"

    def test_is_dispatched_by_the_request_processor(self):
        from app.queue import RedisRequestQueue

        source = inspect.getsource(RedisRequestQueue._process_request)
        assert 'task_type == "api_transcribe"' in source

    def test_is_excluded_from_the_usage_account(self):
        from app.queue import RedisRequestQueue

        source = inspect.getsource(RedisRequestQueue._process_request)
        block = source.split("if task_type not in (")[1].split("):")[0]
        assert "api_transcribe" in block

    def test_returns_the_transcript(self):
        audio = Mock()
        audio.transcribe.return_value = "spoken words"
        result = self._queue(self._app(audio), audio)._process_api_transcribe_task(
            self._task(file_data="AAAA", file_type="audio/wav", file_name="a.wav")
        )
        assert result["status"] == "completed"
        assert result["text"] == "spoken words"
        audio.transcribe.assert_called_once_with("AAAA", "audio/wav", "a.wav", lang="ru")

    def test_transcript_result_is_not_published_to_user_sse(self, monkeypatch):
        from contextlib import nullcontext
        from unittest.mock import MagicMock

        from app.queue import RedisRequestQueue

        q = RedisRequestQueue.__new__(RedisRequestQueue)
        q.app = MagicMock()
        q.app.config = {"REDIS_RESULT_TTL": 3600}
        q.app.logger = Mock()
        q.app.app_context.return_value = nullcontext()
        q.redis = Mock()
        q.results_key = "results"
        q.processing_key = "processing"
        q.slow_processing_key = "slow_processing"
        q.user_requests_key = "user_requests"
        q.queue_key = "queue"
        q._process_request = Mock(return_value={"status": "completed", "text": "sensitive transcript"})
        q._publish_result_event = Mock()
        q._get_model_for_task = Mock(return_value="none")
        q._serialize = json.dumps
        q._cleanup_progress = Mock()
        q._process_single_task(TestTranscriptionQueueTask._task(file_data="AAAA"), "processing")
        q._publish_result_event.assert_not_called()

    def test_unexpected_task_exception_is_sanitized_in_redis_result(self):
        from contextlib import nullcontext
        from unittest.mock import MagicMock

        from app.queue import RedisRequestQueue

        q = RedisRequestQueue.__new__(RedisRequestQueue)
        q.app = MagicMock()
        q.app.config = {"REDIS_RESULT_TTL": 3600}
        q.app.modules = {"base": Mock(**{"_": lambda msg, lang="ru": msg})}
        q.app.logger = Mock()
        q.app.app_context.return_value = nullcontext()
        q.redis = Mock()
        q.results_key = "results"
        q.processing_key = "processing"
        q.slow_processing_key = "slow_processing"
        q.user_requests_key = "user_requests"
        q.queue_key = "queue"
        q._process_request = Mock(side_effect=RuntimeError("private worker path"))
        q._publish_result_event = Mock()
        q._get_model_for_task = Mock(return_value="none")
        q._serialize = json.dumps
        q._cleanup_progress = Mock()

        q._process_single_task(TestTranscriptionQueueTask._task(file_data="AAAA"), "processing")

        result = json.loads(q.redis.hset.call_args.args[2])
        assert "private worker path" not in result["error"]
        assert result["error"].startswith("⚠️ ")
        q._publish_result_event.assert_not_called()

    def test_saves_no_chat_message(self, monkeypatch):
        saved = []
        monkeypatch.setattr("app.queue.save_message", lambda *a, **k: saved.append(a))
        audio = Mock()
        audio.transcribe.return_value = "text"
        self._queue(self._app(audio), audio)._process_api_transcribe_task(
            self._task(file_data="AAAA", file_type="audio/wav", file_name="a.wav")
        )
        assert saved == []

    def test_missing_audio_module_is_an_error(self):
        app = Mock()
        app.modules = {"base": Mock(**{"_": lambda text, lang=None: text})}
        result = self._queue(app, None)._process_api_transcribe_task(
            self._task(file_data="AAAA", file_type="audio/wav", file_name="a.wav")
        )
        assert result["is_error"] is True
        assert result["error"].startswith("⚠️ ")

    def test_unrecognizable_audio_is_an_error(self):
        audio = Mock()
        audio.transcribe.return_value = None
        result = self._queue(self._app(audio), audio)._process_api_transcribe_task(
            self._task(file_data="AAAA", file_type="audio/wav", file_name="a.wav")
        )
        assert result["is_error"] is True
        assert result["error"].startswith("⚠️ ")

    def test_whisper_exception_is_not_exposed(self):
        audio = Mock()
        audio.transcribe.side_effect = RuntimeError("private whisper backend path")
        result = self._queue(self._app(audio), audio)._process_api_transcribe_task(
            self._task(file_data="AAAA", file_type="audio/wav", file_name="a.wav")
        )
        assert result["is_error"] is True
        assert result["error"].startswith("⚠️ ")
        assert "private whisper backend path" not in result["error"]

    def test_missing_audio_data_is_rejected_before_calling_whisper(self):
        audio = Mock()
        result = self._queue(self._app(audio), audio)._process_api_transcribe_task(self._task(file_name="a.wav"))
        assert result["is_error"] is True
        audio.transcribe.assert_not_called()


@pytest.mark.unit
class TestCapabilityFlags:
    @pytest.fixture(autouse=True)
    def _install_audio_tts(self, test_app):
        from unittest.mock import Mock

        tts = Mock()
        tts.available = True
        audio = Mock()
        audio.available = True
        test_app.modules["tts"] = tts
        test_app.modules["audio"] = audio

    def test_audio_flags_are_advertised_once_registered(self, client, api_user):
        body = client.get("/v1/flai/me", headers=bearer(api_user)).get_json()
        assert body["capabilities"]["audio_speech"] is True
        assert body["capabilities"]["audio_transcriptions"] is True

    def test_flags_do_not_claim_media_endpoints(self, client, api_user):
        body = client.get("/v1/flai/me", headers=bearer(api_user)).get_json()
        assert body["capabilities"]["images"] is False
        assert body["capabilities"]["videos"] is False

    def test_audio_endpoints_are_advertised_in_models(self, client, api_user):
        ids = {m["id"] for m in client.get("/v1/models", headers=bearer(api_user)).get_json()["data"]}
        assert {"flai-tts", "flai-stt"} <= ids


@pytest.mark.unit
def test_wav_fixture_is_valid_riff():
    """The suite must not accidentally assert on an empty body."""
    assert WAV.startswith(b"RIFF") and b"WAVE" in WAV


@pytest.mark.unit
def test_speech_payload_is_json_only(client, api_user, test_app):
    """A multipart body on the speech endpoint is a client mistake, not a 500."""
    install_tts(test_app, tts_module())
    response = client.post(SPEECH, data="input=hi", headers=bearer(api_user))
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_body"
    assert json.dumps(response.get_json())  # body is JSON, not an HTML page
