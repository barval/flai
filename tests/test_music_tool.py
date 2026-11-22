"""The chat tool enqueues a music task; it never renders inside the turn."""

from app.tools import _exec_generate_music


class _Module:
    default_duration_s = 30
    max_duration_s = 600


class _Queue:
    def __init__(self):
        self.calls = []

    def add_request(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class _App:
    def __init__(self):
        self.modules = {"music": _Module()}
        self.request_queue = _Queue()


def _ctx(app):
    return {
        "app": app,
        "user_id": "alice",
        "current_session_id": "s-1",
        "user_class": 2,
        "lang": "ru",
    }


def test_a_song_is_queued_not_rendered():
    app = _App()
    answer = _exec_generate_music(
        _ctx(app), prompt="pop ballad", lyrics="[verse]\nline one\n[chorus]\nline two", duration_s=30
    )
    assert "⚠️" not in answer
    assert app.request_queue.calls, "the tool must enqueue a task"
    args, kwargs = app.request_queue.calls[0]
    request_data = args[2]
    assert request_data["type"] == "music_gen"
    assert request_data["duration"] == 30
    assert request_data["lyrics"].startswith("[verse]")
    assert kwargs.get("task_type") == "music_gen"


def test_the_message_quotes_the_person_not_the_style_prompt():
    app = _App()
    ctx = _ctx(app)
    ctx["message_text"] = "Сочини песню про дождь в ночном городе"
    _exec_generate_music(ctx, prompt="night city rain, female vocal", lyrics="дождь\nгород", duration_s=30)
    request_data = app.request_queue.calls[0][0][2]
    assert request_data["text"] == "Сочини песню про дождь в ночном городе"
    assert request_data["prompt"] == "night city rain, female vocal"


def test_missing_lyrics_is_refused():
    app = _App()
    answer = _exec_generate_music(_ctx(app), prompt="pop", lyrics="   ")
    assert answer.startswith("⚠️")
    assert not app.request_queue.calls


def test_duration_outside_the_range_is_refused():
    app = _App()
    answer = _exec_generate_music(_ctx(app), prompt="pop", lyrics="one\ntwo", duration_s=5)
    assert answer.startswith("⚠️")
    assert not app.request_queue.calls


def test_missing_module_is_refused():
    app = _App()
    app.modules = {}
    answer = _exec_generate_music(_ctx(app), prompt="pop", lyrics="one\ntwo")
    assert answer.startswith("⚠️")


def test_a_long_lyric_is_given_a_longer_song():
    app = _App()
    lyrics = "\n".join(f"строка {i}" for i in range(14))
    _exec_generate_music(_ctx(app), prompt="pop", lyrics=lyrics, duration_s=30)
    request_data = app.request_queue.calls[0][0][2]
    assert request_data["duration"] >= 70, "14 lines need more than 30 seconds"
