"""Guard the committed demo pages: drift, size, offline assets and language purity."""

import re
from pathlib import Path

import pytest

from demo.build import build_all

PAGES = Path("docs/demo")
NAMES = ["chat-ru.html", "chat-en.html", "admin-ru.html", "admin-en.html"]
CYRILLIC = re.compile(r"[\u0400-\u04FF]")

if not PAGES.exists():
    pytest.skip("demo pages are produced in Task 12", allow_module_level=True)


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    build_all(out)
    return out


@pytest.mark.parametrize("name", NAMES)
def test_committed_pages_are_current(rebuilt, name):
    assert (rebuilt / name).read_bytes() == (PAGES / name).read_bytes(), (
        f"{name} is stale — run python -m demo.build"
    )


@pytest.mark.parametrize("name", NAMES)
def test_pages_are_self_contained(name):
    html = (PAGES / name).read_text("utf-8")
    assert "<script src=" not in html
    assert 'href="/static/' not in html
    assert "@import" not in html
    assert "url(http" not in html


@pytest.mark.parametrize("name", NAMES)
def test_pages_stay_under_the_budget(name):
    assert (PAGES / name).stat().st_size <= 5 * 1024 * 1024


@pytest.mark.parametrize("name", ["chat-en.html", "admin-en.html"])
def test_english_pages_have_no_cyrillic_text(name):
    html = (PAGES / name).read_text("utf-8")
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", "", html, flags=re.S)
    text = text.replace("Русский", "").replace("Ру", "")
    assert not CYRILLIC.search(text)


@pytest.mark.parametrize("name", ["chat-ru.html", "admin-ru.html"])
def test_russian_pages_keep_their_language(name):
    assert CYRILLIC.search((PAGES / name).read_text("utf-8"))


def test_login_and_notice_are_present():
    for name in NAMES:
        html = (PAGES / name).read_text("utf-8")
        assert "data-demo-login" in html
        assert "демонстрация" in html or "demo" in html.lower()


def test_video_is_inlined_and_playable():
    html = (PAGES / "chat-ru.html").read_text("utf-8")
    assert "data:video/mp4;base64," in html
    assert "<video" in html
