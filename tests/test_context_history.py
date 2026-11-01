"""The model's history must not contain a system notice as an assistant turn."""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


def _rows():
    return [
        {"id": 1, "role": "user", "content": "какие сегодня новости?", "model_type": None},
        {
            "id": 2,
            "role": "assistant",
            "content": "Поисковые сервисы временно недоступны из-за ограничений внешних поисковиков.",
            "model_type": "system",
        },
        {"id": 3, "role": "user", "content": "а курс доллара?", "model_type": None},
        {"id": 4, "role": "assistant", "content": "Курс около 80 рублей.", "model_type": "multimodal"},
    ]


def test_a_system_notice_is_not_an_assistant_turn():
    """The chat model read the notice as an answer and copied it into its reply."""
    from app.db import get_session_text_history

    with patch("app.db.get_session_messages", return_value=_rows()):
        messages = get_session_text_history("s1", max_messages=10)

    contents = [str(message.get("content", "")) for message in messages]
    assert not any("Поисковые сервисы" in text for text in contents)
    assert any("Курс около 80 рублей" in text for text in contents)


def test_ordinary_answers_survive_the_notice_filter():
    from app.db import get_session_text_history

    rows = [row for row in _rows() if row["id"] != 2]
    with patch("app.db.get_session_messages", return_value=rows):
        messages = get_session_text_history("s1", max_messages=10)

    assert [message["id"] for message in messages] == [1, 3, 4]
