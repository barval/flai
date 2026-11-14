"""The camera answers about a snapshot; it never classifies an image request.

Measured regression: "how are things in the office" reached the camera path,
the module used the image-chat classifier prompt, the model picked the edit
variant and the answer saved to the chat (and returned over the API) was the
marker `[-IMAGE-EDIT-] <the question>`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.utils import format_prompt

ROOT = Path(__file__).resolve().parents[1]


def _camera_prompt(lang: str) -> str:
    prompt = format_prompt(
        "camera_question.template",
        {
            "current_time_str": "14.11.2026 21:33",
            "user_query": "Как дела в кабинете",
            "response_language": "Russian",
            "response_style": "",
        },
        lang=lang,
    )
    assert prompt, f"{lang}: the camera template does not render"
    return prompt


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_the_camera_prompt_offers_no_marker_contract(lang):
    prompt = _camera_prompt(lang)
    assert "[-" not in prompt, "a literal marker invites the model to echo it"
    assert "Как дела в кабинете" in prompt


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_the_camera_template_exists_in_both_languages(lang):
    assert (ROOT / "prompts" / lang / "camera_question.template").is_file()


def test_the_image_chat_classifier_keeps_its_markers():
    """The chat path still needs the contract the CPU fix must not remove."""
    template = (ROOT / "prompts" / "ru" / "image_text.template").read_text(encoding="utf-8")
    assert "[-IMAGE-EDIT-]" in template
    assert "[-IMAGE-]" in template
    assert "[-VIDEO-]" in template


def test_both_camera_paths_ask_in_camera_mode():
    source = (ROOT / "app" / "queue.py").read_text(encoding="utf-8")
    start = source.index("def _process_camera_task(")
    end = source.index("def _camera_analysis_width(")
    single_room = source[start:end]
    assert 'mode="camera"' in single_room
    assert "[-IMAGE-EDIT-]" in single_room, "the marker guard must stay as a safety net"

    all_rooms_start = source.index("def _process_camera_all_rooms(")
    all_rooms = source[all_rooms_start:]
    assert 'mode="camera"' in all_rooms

    stream_start = source.index("def _process_camera_task_stream(")
    stream = source[stream_start:]
    assert 'mode="camera"' in stream
    assert "[-IMAGE-EDIT-]" in stream, "the streamed answer must drop a marker before it is saved"
