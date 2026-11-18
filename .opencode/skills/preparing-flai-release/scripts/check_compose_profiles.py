#!/usr/bin/env python3
"""Check compose profiles in both directions.

``check_doc_claims.py`` catches a documented profile that no compose file
defines. The other direction is just as real and had no guard: a profile that
exists and is documented nowhere. Measured — ``with-tts-kokoro`` starts a
container and is named in no README, no ``AGENTS.md`` and no ``docs/`` page, so
a person reading the documentation cannot know the profile exists.

This script reports:

* **undocumented** — a profile a compose file defines that no user-facing
  document mentions;
* **missing** — a profile a document mentions that no compose file defines
  (the same finding as ``check_doc_claims.py``, kept here so one command answers
  both halves).

It also names the compose files and prints the validation command; it does not
run Docker itself.

Exit codes:
    0  every profile resolves both ways
    1  at least one finding
    2  usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DOCUMENTS = ("README.md", "README-ru.md", "AGENTS.md")
# A profile, not a pattern (`with-voice-*`), an anchor (`#-working-with-...`) or a
# fragment of one (`with-tts-` inside a sentence about the family).
PROFILE = re.compile(r"(?<![-#/\w])(with-[a-z0-9]+(?:-[a-z0-9]+)*)(?![\w*-])")


def _defined(root: Path) -> dict[str, str]:
    """profile -> the compose file that defines it."""
    found: dict[str, str] = {}
    for path in sorted(root.glob("docker-compose*.yml")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for block in re.findall(r"profiles:\s*\n((?:\s*-\s*\S+\n?)+)", text):
            for name in re.findall(r"-\s*([A-Za-z0-9_-]+)", block):
                found.setdefault(name, path.name)
        for inline in re.findall(r"profiles:\s*\[([^\]]+)\]", text):
            for name in (item.strip().strip("\"'") for item in inline.split(",")):
                if name:
                    found.setdefault(name, path.name)
    return found


def _documented(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    pages = [root / name for name in DOCUMENTS]
    pages += sorted((root / "docs").glob("*.md"))
    for page in pages:
        if not page.exists():
            continue
        for name in PROFILE.findall(page.read_text(encoding="utf-8", errors="ignore")):
            found.setdefault(name, str(page.relative_to(root)))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    if not list(root.glob("docker-compose*.yml")):
        print(f"no compose files in {root}", file=sys.stderr)
        return 2

    defined = _defined(root)
    documented = _documented(root)

    undocumented = sorted(set(defined) - set(documented))
    missing = sorted(set(documented) - set(defined))

    if args.json:
        print(
            json.dumps(
                {
                    "undocumented": [{"profile": p, "defined_in": defined[p]} for p in undocumented],
                    "missing": [{"profile": p, "mentioned_in": documented[p]} for p in missing],
                },
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        for name in undocumented:
            print(f"UNDOCUMENTED  {name}  defined in {defined[name]}, named in no README/AGENTS/docs")
        for name in missing:
            print(f"MISSING       {name}  mentioned in {documented[name]}, defined in no compose file")
        print(f"\n{len(undocumented)} undocumented, {len(missing)} missing profile(s)")
        print("Validate the files themselves before shipping:")
        for path in sorted(root.glob("docker-compose*.yml")):
            print(f"  docker compose -f {path.name} config >/dev/null")

    return 1 if undocumented or missing else 0


if __name__ == "__main__":
    sys.exit(main())
