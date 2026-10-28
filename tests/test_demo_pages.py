"""Guard the committed demo pages: drift, size, offline assets and language purity."""

import json
import re
from pathlib import Path

import pytest

from demo.build import build_all

PAGES = Path("docs/demo")
DEMO = Path("demo")
NAMES = ["chat-ru.html", "chat-en.html", "admin-ru.html", "admin-en.html"]
CYRILLIC = re.compile(r"[\u0400-\u04FF]")
EN_SNAPSHOT = Path("demo/snapshot/en.json")

pytestmark = pytest.mark.skipif(not PAGES.exists(), reason="demo pages are produced in Task 12")


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
    assert (PAGES / name).stat().st_size <= 16 * 1024 * 1024


@pytest.mark.parametrize("name", ["chat-en.html", "admin-en.html"])
def test_english_pages_have_no_cyrillic_text(name):
    html = (PAGES / name).read_text("utf-8")
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", "", html, flags=re.S)
    text = text.replace("Русский", "").replace("Ру", "")
    assert not CYRILLIC.search(text)


@pytest.mark.parametrize("name", ["chat-ru.html", "admin-ru.html"])
def test_russian_pages_keep_their_language(name):
    assert CYRILLIC.search((PAGES / name).read_text("utf-8"))


def test_english_snapshot_has_no_cyrillic():
    """The English demo is rendered from the English snapshot, so Russian text in
    the capture (room names, titles, documents) reaches the page as-is — the page
    guard above cannot see it, because the snapshot lives inside a script."""
    snapshot = json.loads(EN_SNAPSHOT.read_text("utf-8"))
    leaked = [
        route["path"]
        for route in snapshot["routes"]
        if CYRILLIC.search(json.dumps(route.get("body"), ensure_ascii=False))
    ]
    assert not leaked, f"Russian text in the English snapshot: {leaked}"


def test_login_and_notice_are_present():
    for name in NAMES:
        html = (PAGES / name).read_text("utf-8")
        assert "data-demo-login" in html
        assert "демонстрация" in html or "demo" in html.lower()


def test_video_is_inlined_and_playable():
    html = (PAGES / "chat-ru.html").read_text("utf-8")
    assert "data:video/mp4;base64," in html
    assert "<video" in html


PERSONAL_DATA = re.compile(
    r"password_hash|scrypt:|tavily_key_added_at|tvly-[A-Za-z0-9]|flai_[A-Za-z0-9]{16,}"
)
# The copyright line in the About dialog names the author and is part of the
# interface; a leaked *account* shows up as a login value instead.
REAL_ACCOUNTS = ('"login": "valery"', '"login": "andrey"', '"login": "testuser"')
BASE64_PAYLOAD = re.compile(r"data:[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


def _readable(path: Path) -> str:
    """File text with embedded base64 removed: random runs there look like keys."""
    return BASE64_PAYLOAD.sub("", path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", NAMES)
def test_pages_carry_no_personal_data(name):
    """A demo page is downloadable by anyone: no hashes, key ids or real accounts."""
    html = _readable(PAGES / name)
    assert not PERSONAL_DATA.search(html), f"{name}: personal data leaked into the demo"
    for account in REAL_ACCOUNTS:
        assert account not in html, f"{name}: real account {account} leaked into the demo"


def test_snapshots_carry_no_personal_data():
    for lang in ("ru", "en"):
        text = _readable(DEMO / "snapshot" / f"{lang}.json")
        assert not PERSONAL_DATA.search(text), f"{lang} snapshot: personal data"
        for account in REAL_ACCOUNTS:
            assert account not in text, f"{lang} snapshot: real account {account}"


def test_user_rows_are_rebuilt_not_copied():
    """The captured row must never be copied: it carries a real password hash."""
    from demo.capture import rewrite_for_publication

    snapshot = {
        "routes": [
            {
                "path": "/admin/api/users",
                "body": [{"password_hash": "scrypt:32768:8:1$abc", "tavily_key_added_at": "x", "login": "valery"}],
            }
        ]
    }
    row = rewrite_for_publication(snapshot, "en")["routes"][0]["body"][0]
    assert "password_hash" not in row
    assert "tavily_key_added_at" not in row
    assert row["login"] == "alex"


def test_scrubber_drops_credentials_and_keeps_the_interface_fields():
    from demo.capture import _scrub

    cleaned = _scrub(
        {
            "rows": [
                {
                    "login": "andrey",
                    "password_hash": "scrypt:32768:8:1$abc$def",
                    "tavily_key_added_at": "Sat, 03 Oct 2026 20:07:32 GMT",
                    "has_tavily_key": False,
                    "api_keys_count": 2,
                    "note": "token ghp_abcdefghijklmnopqrstuvwxyz",
                }
            ]
        }
    )
    row = cleaned["rows"][0]
    assert "password_hash" not in row
    assert "tavily_key_added_at" not in row
    assert row["login"] == "andrey"
    assert row["has_tavily_key"] is False
    assert row["api_keys_count"] == 2
    assert row["note"] == ""
