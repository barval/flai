# demo/edit.py
"""Surgical edits of a captured demo snapshot.

A full capture drives the whole product for twenty minutes per language and is
the wrong tool for "the café clip came out badly". These helpers rewrite one
scenario inside ``demo/snapshot/<lang>.json`` — either from a fresh partial
capture or from a file produced by hand.

Everything here is pure data manipulation: no HTTP, no database, so it runs in
milliseconds and cannot break an already captured page.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

DEMO_DIR = Path("demo")
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}
MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def load_snapshot(lang: str) -> dict[str, Any]:
    return json.loads((DEMO_DIR / "snapshot" / f"{lang}.json").read_text(encoding="utf-8"))


def save_snapshot(lang: str, snapshot: dict[str, Any]) -> None:
    path = DEMO_DIR / "snapshot" / f"{lang}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")


def find_scenario_route(snapshot: dict[str, Any], prompt: str) -> dict[str, Any] | None:
    """The route whose body holds this scenario's prompt."""
    needle = prompt[:60]
    for route in snapshot.get("routes", []):
        body = route.get("body")
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            continue
        if needle in json.dumps(body, ensure_ascii=False):
            return route
    return None


def _store_media(lang: str, source: Path) -> tuple[str, str]:
    """Copy a file into demo/media/<lang> and return (asset key, relative path)."""
    suffix = source.suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        raise ValueError(f"unsupported demo media: {source.suffix}")
    digest = hashlib.sha1(source.read_bytes()).hexdigest()
    name = f"injected-{digest[:16]}{suffix}"
    target = DEMO_DIR / "media" / lang / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    return name, f"media/{lang}/{name}"


def inject_media(
    lang: str,
    prompt: str,
    media: Path,
    *,
    answer: str = "",
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Put a ready image/video into a scenario, replacing its answer.

    The user can generate the artifact however they like (the web UI, an external
    tool) and hand the file over: the scenario keeps its prompt and session, and
    the answer becomes the artifact instead of whatever the automatic run made.
    """
    snap = snapshot or load_snapshot(lang)
    route = find_scenario_route(snap, prompt)
    if route is None:
        raise LookupError(f"scenario not found in the {lang} snapshot: {prompt[:40]}")

    asset_key, relative = _store_media(lang, media)
    payload = media.read_bytes()
    snap.setdefault("assets", {})[asset_key] = relative

    body = route.get("body")
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
        raise ValueError(f"route {route.get('path')} has no messages to edit")

    # Drop the old answer(s), keep the user turn that carries the prompt.
    user_index = next(
        (i for i, m in enumerate(messages) if m.get("role") == "user" and prompt[:40] in json.dumps(m.get("content"), ensure_ascii=False)),
        None,
    )
    if user_index is None:
        raise LookupError(f"no user turn for the scenario in {route.get('path')}")
    kept = messages[: user_index + 1]
    kept.append(
        {
            "role": "assistant",
            "content": answer,
            "file_type": MIME_BY_SUFFIX[media.suffix.lower()],
            "file_name": media.name,
            "file_path": asset_key,
            "file_size": len(payload),
            "model_name": "injected",
            "mm_time": None,
            "gen_time": None,
            "response_time": None,
        }
    )
    body["messages"] = kept
    return snap


def data_url(path: Path) -> str:
    """Inline a file the way the builder expects to find it."""
    return f"data:{MIME_BY_SUFFIX[path.suffix.lower()]};base64,{base64.b64encode(path.read_bytes()).decode()}"


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from demo.build import build_all
    from demo.io import DEMO_DIR as DEMO_ROOT

    parser = argparse.ArgumentParser(description="Inject a ready artifact into a demo scenario")
    parser.add_argument("--lang", required=True, choices=("ru", "en"))
    parser.add_argument("--prompt", required=True, help="the scenario prompt (a unique prefix is enough)")
    parser.add_argument("--file", required=True, type=Path, help="image or video to put into the answer")
    parser.add_argument("--answer", default="", help="text that goes with the artifact")
    parser.add_argument("--no-build", action="store_true", help="only edit the snapshot")
    args = parser.parse_args(argv)

    snap = inject_media(args.lang, args.prompt, args.file, answer=args.answer)
    save_snapshot(args.lang, snap)
    print(f"{args.lang}: injected {args.file.name}")
    if not args.no_build:
        build_all(DEMO_ROOT)
        print("pages rebuilt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
