"""Tests for the demo capture client."""

import pytest

from demo.capture import CaptureTimeoutError, DemoSession, rewrite_for_publication, text_found, wait_for


class Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakeTransport:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        for suffix, response in self.responses.items():
            if url.endswith(suffix):
                return response
        return Response(200, {"status": "ok"})


def test_session_fetches_csrf_after_login():
    transport = FakeTransport({"/chat": Response(200, text='<meta name="csrf-token" content="tok">')})
    session = DemoSession("http://demo", transport=transport)
    session.login("demo_ru_chat", "secret")
    assert session.csrf_token == "tok"


def test_session_sends_the_token_on_post():
    transport = FakeTransport({"/chat": Response(200, text='<meta name="csrf-token" content="tok">')})
    session = DemoSession("http://demo", transport=transport)
    session.login("demo_ru_chat", "secret")
    session.post_json("/api/sessions/new", {})
    headers = transport.calls[-1]["kwargs"].get("headers", {})
    assert headers.get("X-CSRFToken") == "tok"


def test_publication_replaces_real_users():
    snapshot = {"routes": [{"path": "/admin/api/users", "body": [{"login": "real", "name": "Real"}]}]}
    out = rewrite_for_publication(snapshot, lang="ru")
    assert [user["login"] for user in out["routes"][0]["body"]] == ["anna", "igor", "maria"]
    assert [user["name"] for user in out["routes"][0]["body"]] == ["Анна", "Игорь", "Мария"]


def test_publication_replaces_monitoring_series():
    snapshot = {"routes": [{"path": "/admin/api/monitoring", "body": {"series": {"messages": [99, 98]}}}]}
    out = rewrite_for_publication(snapshot, lang="en")
    assert out["routes"][0]["body"]["series"]["messages"] != [99, 98]


def test_wait_for_raises_on_timeout():
    with pytest.raises(CaptureTimeoutError):
        wait_for(lambda: False, timeout_s=0.05, interval_s=0.01)


def test_wait_for_returns_when_ready():
    calls = {"count": 0}

    def ready():
        calls["count"] += 1
        return calls["count"] >= 2

    assert wait_for(ready, timeout_s=1, interval_s=0.01) is True


def test_text_found_reads_the_yes_no_verdict():
    assert text_found("ДА") is True
    assert text_found("Да, на обложке слово SALE") is True
    assert text_found("NO") is False
    assert text_found("НЕТ") is False
    assert text_found("На изображении нет текста.") is False


def test_text_found_treats_an_unclear_answer_as_a_defect():
    assert text_found("") is True
    assert text_found("Мне нужно подумать") is True


def test_image_paths_collects_images_only_and_keeps_order():
    from demo.capture import _image_paths

    payload = {
        "messages": [
            {"file_path": "a/one.png"},
            {"extra": {"final_items": [{"file_path": "b/two.webp"}, {"file_path": "c/clip.mp4"}]}},
            {"file_path": "a/one.png"},
        ]
    }
    assert _image_paths(payload) == ["a/one.png", "b/two.webp"]


def test_compress_images_replaces_png_with_webp(tmp_path):
    from PIL import Image

    from demo.capture import compress_images

    folder = tmp_path / "media" / "ru"
    folder.mkdir(parents=True)
    Image.new("RGB", (8, 8), (200, 120, 40)).save(folder / "shot.png")
    media = {"/api/files/shot.png": "media/ru/shot.png"}

    compress_images(media, base_dir=tmp_path)

    assert media == {"/api/files/shot.png": "media/ru/shot.webp"}
    assert (folder / "shot.webp").is_file()
    assert not (folder / "shot.png").exists()


def test_publication_drops_the_empty_default_session():
    snapshot = {
        "routes": [
            {
                "path": "/api/sessions",
                "body": [
                    {"id": "a", "title": "Real", "message_count": 2},
                    {"id": "b", "title": "", "message_count": 0},
                ],
            }
        ]
    }
    out = rewrite_for_publication(snapshot, lang="ru")
    assert [session["id"] for session in out["routes"][0]["body"]] == ["a"]
