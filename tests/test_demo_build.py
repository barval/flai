"""Tests for the demo page builder and its check mode."""

from pathlib import Path

from demo.build import build_all, main

FIXTURE = Path(__file__).parent / "fixtures" / "demo_snapshot"


def test_build_writes_four_deterministic_files(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    build_all(first, FIXTURE)
    build_all(second, FIXTURE)
    names = sorted(p.name for p in first.iterdir())
    assert names == ["admin-en.html", "admin-ru.html", "chat-en.html", "chat-ru.html"]
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()
        assert (first / name).stat().st_size > 10_000


def test_check_reports_a_current_build(tmp_path):
    out = tmp_path / "out"
    build_all(out, FIXTURE)
    assert main(["--out", str(out), "--demo-dir", str(FIXTURE), "--check"]) == 0


def test_check_detects_a_stale_page(tmp_path, capsys):
    out = tmp_path / "out"
    build_all(out, FIXTURE)
    (out / "chat-ru.html").write_text("stale", "utf-8")
    assert main(["--out", str(out), "--demo-dir", str(FIXTURE), "--check"]) == 1
    assert "chat-ru.html" in capsys.readouterr().out


def test_built_pages_carry_the_authenticated_header(tmp_path):
    out = tmp_path / "out"
    build_all(out, FIXTURE)
    chat = (out / "chat-ru.html").read_text("utf-8")
    admin = (out / "admin-ru.html").read_text("utf-8")
    assert "theme-toggle" in chat
    assert "theme-toggle" in admin
