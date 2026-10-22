"""Multi-audio chat support: one voice recording plus several audio files
(total up to four) are transcribed in order and answered once.

A chat message with several audio files kept only the FIRST one (the legacy
single slot) and dropped the rest without a word — not on disk, not in the
content JSON, never transcribed. An audio FILE next to a typed question was
transcribed and then never answered, because the answer was gated on the
voice-recording flag. The route collects every audio item now, the queue
transcribes them all in order and always continues with the answer.
"""

import base64
import io
import json as _json
import os

AUDIO = (
    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    + b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)
AUDIO_B64 = base64.b64encode(AUDIO).decode()
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


class TestTranscribeTaskAudioList:
    """app/queue.py: _process_transcribe_task over a list of audio items."""

    def _run(self, monkeypatch, request_data, transcripts=("text",)):
        """Run the handler with a scripted transcriber.

        Returns ``(calls, saved, requeued, result)``: the file name of every
        transcription call in order, every message the handler saved, every
        ``add_request`` payload it queued and its own return value.
        """
        import app.queue as queue_mod

        q = queue_mod.RedisRequestQueue.__new__(queue_mod.RedisRequestQueue)
        calls: list[str] = []

        class _Audio:
            @staticmethod
            def transcribe(data, file_type=None, file_name=None, **kwargs):
                calls.append(str(file_name))
                index = len(calls) - 1
                return transcripts[index] if index < len(transcripts) else None

        class _Base:
            @staticmethod
            def _(msgid, *a, **k):
                return msgid

        class _Log:
            @staticmethod
            def info(*a, **k):
                pass

            warning = error = info

        q.app = type("A", (), {})()
        q.app.logger = _Log()
        q.app.modules = {"audio": _Audio(), "base": _Base()}
        requeued: list[dict] = []

        def fake_add(user_id, session_id, request_data, user_class, lang="ru", **kwargs):
            requeued.append(request_data)
            return "new-id", {}

        q.app.request_queue = q
        monkeypatch.setattr(q, "add_request", fake_add)
        saved: list[str] = []
        monkeypatch.setattr(queue_mod, "save_message", lambda *a, **k: (saved.append(a[2]), "mid")[1])

        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(queue_mod, "force_locale", lambda loc: _Ctx())
        monkeypatch.setattr(queue_mod, "get_current_time_in_timezone_for_db", lambda app: "t")

        result = q._process_transcribe_task({"user_id": "u1", "session_id": "s1", "data": request_data, "lang": "ru"})
        return calls, saved, requeued, result

    @staticmethod
    def _audio(name):
        return {"data": AUDIO_B64, "type": "audio/wav", "name": name}

    def test_every_audio_item_is_transcribed_in_order(self, monkeypatch):
        calls, saved, requeued, result = self._run(
            monkeypatch,
            {
                "type": "transcribe_audio",
                "audios": [self._audio("first.wav"), self._audio("second.wav"), self._audio("third.wav")],
            },
            transcripts=("one", "two", "three"),
        )

        assert calls == ["first.wav", "second.wav", "third.wav"]
        assert "error" not in result
        assert requeued[0]["type"] == "text"
        text = requeued[0]["text"]
        assert text.index("one") < text.index("two") < text.index("three")
        assert len(saved) == 3

    def test_a_plain_audio_file_is_answered_too(self, monkeypatch):
        """Only a voice RECORDING used to trigger the answer: a typed question
        next to an audio file got a transcript and nothing else."""
        calls, _saved, requeued, result = self._run(
            monkeypatch,
            {"type": "transcribe_audio", "audios": [self._audio("clip.wav")], "text": "Что здесь сказано?"},
            transcripts=("hello",),
        )

        assert calls == ["clip.wav"]
        assert "error" not in result
        assert requeued[0]["type"] == "text"
        assert requeued[0]["text"].startswith("Что здесь сказано?")
        assert "hello" in requeued[0]["text"]

    def test_the_recording_leads_the_audio_files(self, monkeypatch):
        calls, _saved, requeued, _result = self._run(
            monkeypatch,
            {
                "type": "transcribe_audio",
                "audios": [
                    self._audio("voice.webm"),
                    self._audio("one.wav"),
                    self._audio("two.wav"),
                ],
            },
            transcripts=("spoken", "first", "second"),
        )

        assert calls == ["voice.webm", "one.wav", "two.wav"]
        text = requeued[0]["text"]
        assert text.index("spoken") < text.index("first") < text.index("second")

    def test_an_unreadable_audio_does_not_stop_the_others(self, monkeypatch):
        calls, saved, requeued, result = self._run(
            monkeypatch,
            {
                "type": "transcribe_audio",
                "audios": [self._audio("a.wav"), self._audio("b.wav"), self._audio("c.wav")],
            },
            transcripts=("one", None, "three"),
        )

        assert len(calls) == 3, "the unreadable file must not cut the loop short"
        assert "error" not in result
        assert "one" in requeued[0]["text"]
        assert "three" in requeued[0]["text"]
        assert len(saved) == 2

    def test_the_task_fails_only_when_every_audio_is_empty(self, monkeypatch):
        _calls, saved, requeued, result = self._run(
            monkeypatch,
            {"type": "transcribe_audio", "audios": [self._audio("a.wav"), self._audio("b.wav")]},
            transcripts=(None, ""),
        )

        assert result.get("error"), "an all-empty batch must report the localized error"
        assert requeued == [], "nothing to answer without a transcript"
        assert len(saved) == 1 and "⚠️" in saved[0]

    def test_a_document_and_audio_are_answered_together(self, monkeypatch):
        _calls, _saved, requeued, result = self._run(
            monkeypatch,
            {
                "type": "transcribe_audio",
                "audios": [self._audio("note.wav")],
                "text": "Сверь с отчётом",
                "doc_file_data": base64.b64encode(PDF).decode(),
                "doc_file_type": "application/pdf",
                "doc_file_name": "report.pdf",
            },
            transcripts=("hello",),
        )

        assert "error" not in result
        assert requeued[0]["type"] == "doc_chat"
        assert requeued[0]["doc_file_name"] == "report.pdf"
        assert requeued[0]["text"].startswith("Сверь с отчётом")
        assert "hello" in requeued[0]["text"]

    def test_an_image_pairs_with_the_transcripts(self, monkeypatch):
        _calls, _saved, requeued, _result = self._run(
            monkeypatch,
            {
                "type": "transcribe_audio",
                "audios": [self._audio("clip.wav")],
                "image_data": "AAAA",
                "image_type": "image/png",
                "image_name": "shot.png",
            },
            transcripts=("hello",),
        )

        assert requeued[0]["type"] == "image"
        assert requeued[0]["file_data"] == "AAAA"
        assert "hello" in requeued[0]["text"]

    def test_each_transcript_message_names_its_file(self, monkeypatch):
        _calls, saved, _requeued, _result = self._run(
            monkeypatch,
            {"type": "transcribe_audio", "audios": [self._audio("a.wav"), self._audio("b.wav")]},
            transcripts=("one", "two"),
        )

        prefixes = [_json.loads(content)["prefix"] for content in saved]
        assert prefixes[0].endswith("(a.wav): ")
        assert prefixes[1].endswith("(b.wav): ")

    def test_a_single_audio_keeps_the_plain_label(self, monkeypatch):
        _calls, saved, _requeued, _result = self._run(
            monkeypatch,
            {"type": "transcribe_audio", "audios": [self._audio("a.wav")]},
            transcripts=("one",),
        )

        assert _json.loads(saved[0])["prefix"] == "🎤 Transcribed: "


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


class TestMultiAudioChatRoute:
    """app/routes/messages.py: every audio item of a message reaches the queue.

    The route kept only the FIRST audio part (the legacy single slot) and
    dropped the rest silently — not on disk, not in the content JSON, never
    transcribed — and the voice-recording flag alone decided whether the
    message was answered at all.
    """

    def _login(self, client):
        with client.application.app_context():
            from app.userdb import create_user, get_user_by_login

            if not get_user_by_login("multiaudio"):
                create_user("multiaudio", "pass123", "Multi Audio")

        client.post("/login", data={"login": "multiaudio", "password": "pass123"})
        self._session_id = client.post("/api/sessions/new").get_json()["id"]

    def _audio_file(self, name):
        return (io.BytesIO(AUDIO), name, "audio/wav")

    def _capture(self, monkeypatch, test_app):
        queued: list[dict] = []

        def fake_add(user_id, session_id, request_data, user_class, lang="ru", **kwargs):
            queued.append(request_data)
            return "req-1", {"position": 1, "estimated_seconds": 1, "queue_type": "fast"}

        monkeypatch.setattr(test_app.request_queue, "add_request", fake_add)
        return queued

    def _user_parts(self, test_app):
        with test_app.app_context():
            from app import db as dbmod

            msgs = dbmod.get_session_messages(self._session_id)
        return _json.loads([m for m in msgs if m["role"] == "user"][-1]["content"])

    def test_every_audio_part_is_queued_and_saved(self, client, test_app, monkeypatch):
        self._login(client)
        queued = self._capture(monkeypatch, test_app)

        response = client.post(
            "/api/send_message",
            data={
                "message": "",
                "file": [self._audio_file("one.wav"), self._audio_file("two.wav"), self._audio_file("three.wav")],
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200, response.get_json()
        assert len(queued) == 1
        payload = queued[0]
        assert payload["type"] == "transcribe_audio"
        assert [a["name"] for a in payload["audios"]] == ["one.wav", "two.wav", "three.wav"]

        audio_parts = [p for p in self._user_parts(test_app) if p.get("type") == "audio"]
        assert len(audio_parts) == 3, "every audio part must be listed in the content JSON"
        for part in audio_parts:
            assert part.get("file_path"), "every audio part must be saved to disk"
            assert os.path.exists(os.path.join(test_app.config["UPLOAD_FOLDER"], part["file_path"]))

    def test_at_most_four_audio_items_per_message(self, client, test_app, monkeypatch):
        self._login(client)
        queued = self._capture(monkeypatch, test_app)

        response = client.post(
            "/api/send_message",
            data={"message": "", "file": [self._audio_file(f"{index}.wav") for index in range(5)]},
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        assert "4" in response.get_json()["error"]
        assert queued == [], "a refused message must not be queued"

    def test_a_recording_and_three_audio_files_fit(self, client, test_app, monkeypatch):
        self._login(client)
        queued = self._capture(monkeypatch, test_app)

        response = client.post(
            "/api/send_message",
            data={
                "message": "",
                "voice": (io.BytesIO(AUDIO), "voice.webm", "audio/webm"),
                "voice_record": "true",
                "file": [self._audio_file(f"{index}.wav") for index in range(3)],
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200, response.get_json()
        names = [audio["name"] for audio in queued[0]["audios"]]
        assert names == ["voice.webm", "0.wav", "1.wav", "2.wav"]

    def test_four_audio_files_and_a_recording_are_refused(self, client, test_app, monkeypatch):
        self._login(client)
        queued = self._capture(monkeypatch, test_app)

        response = client.post(
            "/api/send_message",
            data={
                "message": "",
                "voice": (io.BytesIO(AUDIO), "voice.webm", "audio/webm"),
                "voice_record": "true",
                "file": [self._audio_file(f"{index}.wav") for index in range(4)],
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 400
        assert queued == []

    def test_a_typed_question_next_to_an_audio_file_is_carried(self, client, test_app, monkeypatch):
        """The question used to be dropped: the transcribe request carried no
        text at all, so a plain audio file was transcribed and never answered."""
        self._login(client)
        queued = self._capture(monkeypatch, test_app)

        response = client.post(
            "/api/send_message",
            data={"message": "Что здесь сказано?", "file": [self._audio_file("clip.wav")]},
            content_type="multipart/form-data",
        )

        assert response.status_code == 200, response.get_json()
        assert queued[0]["text"] == "Что здесь сказано?"

    def test_a_document_next_to_audio_is_carried(self, client, test_app, monkeypatch):
        """A document used to win the request type: the audio next to it was
        saved and displayed but never transcribed."""
        self._login(client)
        queued = self._capture(monkeypatch, test_app)

        response = client.post(
            "/api/send_message",
            data={
                "message": "Сверь с отчётом",
                "file": [(io.BytesIO(PDF), "report.pdf", "application/pdf"), self._audio_file("note.wav")],
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200, response.get_json()
        payload = queued[0]
        assert payload["type"] == "transcribe_audio"
        assert payload["doc_file_name"] == "report.pdf"
        assert payload["text"] == "Сверь с отчётом"

    def test_a_recording_next_to_an_audio_file_is_not_an_image(self, client, test_app, monkeypatch):
        """Voice (legacy "voice" field) + audio file (multi queue): the audio
        file was handed to the multimodal call as image_data."""
        self._login(client)
        queued = self._capture(monkeypatch, test_app)

        response = client.post(
            "/api/send_message",
            data={
                "message": "",
                "voice": (io.BytesIO(AUDIO), "voice.webm", "audio/webm"),
                "voice_record": "true",
                "file": [self._audio_file("note.wav")],
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200, response.get_json()
        payload = queued[0]
        assert "image_data" not in payload, "audio bytes must never be presented as an image"
        assert [audio["name"] for audio in payload["audios"]] == ["voice.webm", "note.wav"]

    def test_an_image_pairs_with_the_audio(self, client, test_app, monkeypatch):
        self._login(client)
        queued = self._capture(monkeypatch, test_app)

        response = client.post(
            "/api/send_message",
            data={
                "message": "",
                "file": [self._audio_file("clip.wav"), (io.BytesIO(PNG), "shot.png", "image/png")],
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200, response.get_json()
        payload = queued[0]
        assert payload["image_name"] == "shot.png"
        assert payload["image_data"] != payload["audios"][0]["data"]


class TestTaskAudioCap:
    """app/routes/tasks.py: the audio cap is enforced before anything is stored."""

    class _Upload:
        def __init__(self, name, content_type="audio/wav", data=AUDIO):
            self.filename = name
            self.content_type = content_type
            self._data = data

        def read(self):
            return self._data

    def _call(self, test_app, uploads):
        from flask import session

        from app.routes.tasks import _save_task_audio

        with test_app.test_request_context("/api/tasks"):
            session["login"] = "audio-cap"
            session["language"] = "ru"
            return _save_task_audio(uploads, "task-1", "request")

    def test_more_than_four_audio_items_are_refused(self, test_app):
        uploads = [self._Upload(f"{index}.wav") for index in range(5)]

        entries, error, status = self._call(test_app, uploads)

        assert entries is None
        assert status == 400
        assert "4" in error, "the message must name the cap"
