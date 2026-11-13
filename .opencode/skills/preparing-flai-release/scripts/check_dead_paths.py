#!/usr/bin/env python3
"""Report paths named in AGENTS.md and docs/*.md that no longer exist.

Documentation drifts by path, not by sentence: a template is renamed, a module is
split, and the sentence around the old path still reads correctly. Measured: the
architecture summary in AGENTS.md described `memory_extract.template` months
after the live path moved to `memory_triples.template`, and nothing caught it —
no test reads prose.

Read-only. Exit 0 when every mentioned path exists, 1 on findings, 2 on a git or
usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOTS = (
    "app/",
    "modules/",
    "tests/",
    "docs/",
    "prompts/",
    "services/",
    "scripts/",
    "demo/",
    "translations/",
    ".github/",
    ".opencode/",
)
TOKEN = re.compile(r"`([^`\n]{3,160})`")
EXTENSION = re.compile(r"\.[a-z0-9]{1,10}$")
NON_PATH_CHARS = set("*{}<>|$%")


def _is_path_text(text: str) -> bool:
    """A claim about a file in this repository, not a route, image or placeholder."""
    if not text or any(ch in NON_PATH_CHARS for ch in text):
        return False
    if text.startswith(("http://", "https://", "/")):
        # An absolute path is a container path or an HTTP route, neither of which
        # this checkout can confirm.
        return False
    if any(ch in text for ch in (":", "@")):
        # A Docker image (name:tag) or a pinned action (owner/repo@version).
        return False
    if any(ch.isspace() for ch in text):
        return False
    if "/X" in text or text.startswith("X"):
        # Documentation placeholders such as docs/X.md.
        return False
    if text.startswith(ROOTS):
        return bool(EXTENSION.search(text))
    # A bare `name.ext` is prose about a file, not a path claim.
    return "/" in text and bool(EXTENSION.search(text))


def _normalise(text: str) -> str:
    return text.split("#", 1)[0].rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    if not (root / "AGENTS.md").exists():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    documents = [root / "AGENTS.md", *sorted((root / "docs").glob("*.md"))]
    findings: list[tuple[str, str, int]] = []

    for document in documents:
        if not document.exists():
            continue
        for number, line in enumerate(document.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for match in TOKEN.finditer(line):
                token = _normalise(match.group(1).strip())
                if not _is_path_text(token):
                    continue
                if (root / token).exists() or (document.parent / token).exists():
                    continue
                findings.append((str(document.relative_to(root)), token, number))

    if args.json:
        import json

        print(json.dumps([{"file": f, "path": p, "line": n} for f, p, n in findings], ensure_ascii=False, indent=1))
    else:
        for file_name, path, number in findings:
            print(f"MISSING  {file_name}:{number}  {path}")
        print(f"\n{len(findings)} missing path(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
