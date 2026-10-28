"""Build the four self-contained demo pages from the committed snapshot."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from demo.context import admin_context, chat_context
from demo.inline import inline_page
from demo.io import DEMO_DIR, ROOT, load_snapshot
from demo.render import render_app, render_login
from demo.shell import build_shell
from demo.snapshot import compile_assets, compile_routes

LANGS = ("ru", "en")
KINDS = ("chat", "admin")

NOTICE = {
    "ru": "Это демонстрация интерфейса: кнопки неактивны",
    "en": "This is an interface demo: the buttons are inactive",
}

CREDENTIAL_KEY = {"chat": "user", "admin": "admin"}


def build_page(lang: str, kind: str, demo_dir: Path = DEMO_DIR) -> str:
    """Render one page from the snapshot, with no database, network or GPU access."""
    snapshot = load_snapshot(demo_dir / "snapshot" / f"{lang}.json")
    credentials = snapshot.get("credentials", {}).get(CREDENTIAL_KEY[kind], {})
    # The admin pages must show the admin account on the locked login form; the
    # snapshots carry no credentials map, so the fallback is per page kind.
    login_name = credentials.get("login", "admin" if kind == "admin" else "user")

    context = chat_context(snapshot) if kind == "chat" else admin_context(snapshot)
    context["session"] = {
        "login": login_name,
        "is_admin": kind == "admin",
        "language": lang,
        "theme": "light",
    }

    app_html = inline_page(render_app(lang, kind, context), ROOT)
    login_html = inline_page(render_login(lang, login_name), ROOT)

    payload = {
        "lang": lang,
        "notice": NOTICE[lang],
        "routes": compile_routes(snapshot),
        "assets": compile_assets(snapshot, demo_dir) if kind == "chat" else {},
    }
    return build_shell(lang, kind, login_html=login_html, app_html=app_html, payload=payload)


def build_all(out_dir: Path, demo_dir: Path = DEMO_DIR) -> list[Path]:
    """Write the four pages in a fixed order and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for lang in LANGS:
        for kind in KINDS:
            path = out_dir / f"{kind}-{lang}.html"
            path.write_text(build_page(lang, kind, demo_dir), encoding="utf-8", newline="\n")
            written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m demo.build")
    parser.add_argument("--out", default=str(ROOT / "docs" / "demo"))
    parser.add_argument("--demo-dir", default=str(DEMO_DIR))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    demo_dir = Path(args.demo_dir)

    if not args.check:
        for path in build_all(out_dir, demo_dir):
            print(path)
        return 0

    with tempfile.TemporaryDirectory() as raw:
        fresh = Path(raw)
        build_all(fresh, demo_dir)
        drifted = [
            f"{kind}-{lang}.html"
            for lang in LANGS
            for kind in KINDS
            if not (out_dir / f"{kind}-{lang}.html").is_file()
            or (out_dir / f"{kind}-{lang}.html").read_bytes() != (fresh / f"{kind}-{lang}.html").read_bytes()
        ]

    if drifted:
        print("Demo pages are stale: " + ", ".join(drifted))
        return 1
    print("Demo pages are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
