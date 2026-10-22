# tests/test_tasks_audio_transcribe.py
"""Audio attachments of a task: background Whisper transcription.

Chat parity (user request): a voice message or an audio file attached to a
task request or answer is transcribed into text and fed to the task — the
transcript is task CONTEXT (like an image description), never a
Documents-panel document and never indexed. A dictated task (audio without
text) waits for its transcript before the planner runs; a text task with
audio material gets the transcript in every step's context.

The transcription is an HTTP call to the Whisper sidecar (the same
``AudioModule.transcribe`` the chat's ``transcribe_audio`` fast-worker task
uses — no GPU lock), so these tests stub the audio module and the store: no
database, no Redis, no model.
"""

import os
from unittest.mock import MagicMock

import pytest

import app.tasks.store as store
from app.tasks import answer_images
from app.tasks.audio_transcribe import is_transcribing, transcribe_task_audio

pytestmark = [pytest.mark.unit]

TASK_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def audio_env(test_app, monkeypatch):
    """A frozen task with audio entries on disk + a stubbed Whisper module.

    Returns a namespace object carrying the live task dict, the recorded
    ``update_task`` payloads and step appends, so each test can assert on the
    work the transcription did.
    """

    class Env:
        pass

    env = Env()
    env.app = test_app
    env.task = {}
    env.updates = []
    env.step_updates = []
    env.published = []
    env.final_steps = []

    def _setup(entries, transcript="салфетки зелёные", **task_over):
        upload_folder = str(test_app.config["UPLOAD_FOLDER"])
        task_folder = os.path.join(upload_folder, TASK_ID)
        os.makedirs(task_folder, exist_ok=True)
        for entry in entries:
            name = os.path.basename(str(entry.get("file_path") or ""))
            if name:
                with open(os.path.join(task_folder, name), "wb") as fh:
                    fh.write(b"fake-audio-bytes")
            entry.setdefault("file_path", f"{TASK_ID}/{name}")
        env.task = {
            "id": TASK_ID,
            "user_id": "owner",
            "title": "проанализируй аудио",
            "lang": "ru",
            "status": "frozen",
            "abort_reason": "audio_transcript",
            "audio": entries,
        }
        env.task.update(task_over)
        audio = MagicMock()
        audio.transcribe = MagicMock(return_value=transcript)
        monkeypatch.setitem(test_app.modules, "audio", audio)
        env.audio = audio

        monkeypatch.setattr(store, "get_task", lambda tid: dict(env.task) if tid == TASK_ID else None)

        def _update_task(tid, **fields):
            env.updates.append(fields)
            env.task.update(fields)
            return dict(env.task)

        monkeypatch.setattr(store, "update_task", _update_task)
        monkeypatch.setattr(
            store,
            "get_step",
            lambda sid: {"id": sid, "result": "старый ответ", "extra": {}} if sid == "step-1" else None,
        )
        monkeypatch.setattr(store, "update_step", lambda sid, **f: env.step_updates.append((sid, f)))
        monkeypatch.setattr("app.tasks.events.publish_task_update", lambda app, task: env.published.append(task))
        monkeypatch.setattr(
            "app.tasks.executor.add_final_step",
            lambda tid, text, status="passed", meta=None: env.final_steps.append((tid, text, status)),
        )
        # No images are being described for this task unless a test says so.
        monkeypatch.setattr(answer_images, "is_describing", lambda tid: False)
        return env

    env.setup = _setup
    return env


def _entry(**over):
    entry = {
        "file_path": f"{TASK_ID}/voice.webm",
        "file_name": "voice.webm",
        "file_type": "audio/webm",
        "phase": "request",
    }
    entry.update(over)
    return entry


def test_request_transcript_lands_in_the_task_and_requeues(audio_env):
    """A transcribed request audio is stored on the task and the task returns
    to the queue (the planner then reads the transcript as context)."""
    env = audio_env.setup([_entry()])

    transcribe_task_audio(env.app, TASK_ID)

    assert env.task["audio"][0]["transcript"] == "салфетки зелёные"
    assert any(u.get("audio") for u in env.updates), "the transcripts are persisted"
    assert env.updates[-1]["status"] == "queued"
    assert env.updates[-1]["abort_reason"] is None
    assert env.published, "the task view is told to reload"
    assert is_transcribing(TASK_ID) is False


def test_dictated_request_takes_the_title_from_the_transcript(audio_env):
    """A task dictated by voice has no text: the transcript names it (the
    sidebar and the request bubble would otherwise be blank)."""
    env = audio_env.setup([_entry()], title="")

    transcribe_task_audio(env.app, TASK_ID)

    assert env.task["title"] == "салфетки зелёные"


def test_text_request_keeps_its_title(audio_env):
    """Audio attached as MATERIAL must not rename a task that has text."""
    env = audio_env.setup([_entry()])

    transcribe_task_audio(env.app, TASK_ID)

    assert env.task["title"] == "проанализируй аудио"


def test_answer_transcript_is_appended_to_the_answer_step(audio_env):
    """An audio answer feeds the user_input step (the reasoning step reads the
    transcript from the step row — the answer_images contract)."""
    env = audio_env.setup([_entry(phase="answer", step_id="step-1")])

    transcribe_task_audio(env.app, TASK_ID)

    assert env.step_updates, "the answer step got the transcript"
    step_id, fields = env.step_updates[0]
    assert step_id == "step-1"
    assert "салфетки зелёные" in fields["result"]
    assert fields["result"].startswith("старый ответ")
    assert env.updates[-1]["status"] == "awaiting_user"


def test_failed_transcription_fails_the_task(audio_env):
    """Nothing to work with → a localized ⚠️ error, never a silent empty task."""
    env = audio_env.setup([_entry()])
    env.audio.transcribe.return_value = None

    transcribe_task_audio(env.app, TASK_ID)

    assert env.final_steps, "a terminal ⚠️ step is recorded"
    assert env.final_steps[0][1].startswith("⚠️ ")
    assert env.final_steps[0][2] == "failed"
    assert env.task["status"] == "failed"


def test_missing_audio_file_is_a_failure(audio_env):
    """The file vanished from disk (cleanup, task media deleted) → same
    terminal error instead of an endless freeze."""
    env = audio_env.setup([_entry(file_path=f"{TASK_ID}/gone.webm")])
    os.remove(os.path.join(str(env.app.config["UPLOAD_FOLDER"]), TASK_ID, "gone.webm"))

    transcribe_task_audio(env.app, TASK_ID)

    assert env.task["status"] == "failed"


def test_stopped_task_is_not_requeued(audio_env):
    """The user stopped (or deleted) the task while Whisper ran: its status is
    terminal and must never be flipped back to queued."""
    env = audio_env.setup([_entry()], status="cancelled", abort_reason=None)

    transcribe_task_audio(env.app, TASK_ID)

    assert env.task["status"] == "cancelled"
    assert not any(u.get("status") for u in env.updates), "no status write on a terminal task"


def test_waits_for_the_answer_image_description(audio_env, monkeypatch):
    """Images AND audio on the same answer: the last worker restores the
    awaiting state, so the executor can never consume a half-enriched answer."""
    env = audio_env.setup([_entry(phase="answer", step_id="step-1")])
    monkeypatch.setattr(answer_images, "is_describing", lambda tid: tid == TASK_ID)

    transcribe_task_audio(env.app, TASK_ID)

    assert not any(u.get("status") for u in env.updates), "the image thread still owns the freeze"


def test_audio_module_unavailable_fails_the_task(test_app, monkeypatch, audio_env):
    env = audio_env.setup([_entry()])
    env.audio.transcribe.side_effect = RuntimeError("whisper down")

    transcribe_task_audio(env.app, TASK_ID)

    assert env.task["status"] == "failed"
