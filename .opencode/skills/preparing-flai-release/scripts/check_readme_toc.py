#!/usr/bin/env python3
"""Verify the README table of contents against the rendered heading anchors.

Read-only. Run from the repository root.

Every entry between the ``<!-- TOC:BEGIN -->`` and ``<!-- TOC:END -->`` markers
must match the ``##`` headings in the same order: the label must be the heading
text and the anchor must be the anchor the renderer (GitHub, Gitea) will
generate for it.

The slug is computed independently of ``scripts/update_toc.py`` on purpose: a
shared implementation would share its bugs. The rule both follow is documented
in ``docs/DEVELOPMENT.md`` ("README table of contents"):

- everything except letters, digits, ``_`` and ``-`` is dropped (an emoji
  leaves a space behind, so the anchor starts with a hyphen);
- each remaining space becomes exactly one hyphen — spaces are never collapsed,
  so a removed ``&`` yields ``--``;
- the text is lowercased first and is never truncated.

Exit codes:
    0  every table of contents entry resolves and matches its heading
    1  a missing or stale entry, a mismatched anchor, or missing markers
    2  usage or file error
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

BEGIN = "<!-- TOC:BEGIN -->"
END = "<!-- TOC:END -->"
DEFAULT_FILES = ("README.md", "README-ru.md")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    message: str


def strip_inline_markdown(text: str) -> str:
    """Drop the markdown syntax the renderer hides before computing an anchor."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "")
    text = re.sub(r"\*+", "", text)
    text = text.replace("~~", "")
    return text


def heading_slug(heading: str) -> str:
    """Return the anchor GitHub/Gitea generate for a heading."""
    slug: list[str] = []
    for char in strip_inline_markdown(heading).strip().lower():
        if char == " ":
            slug.append("-")
        elif char.isalnum() or char in {"_", "-"} or unicodedata.category(char).startswith("M"):
            slug.append(char)
    return "".join(slug)


def check(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    if not path.exists():
        return [Finding(str(path), 0, "file is missing")]
    text = path.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        return [Finding(str(path), 0, "TOC markers are missing")]

    before, rest = text.split(BEGIN, 1)
    block, _ = rest.split(END, 1)
    toc_offset = before.count("\n") + 1

    entries: list[tuple[str, str, int]] = []
    for number, line in enumerate(block.splitlines(), start=toc_offset + 1):
        match = re.match(r"^- \[(.+)\]\(#(.*)\)\s*$", line)
        if match:
            entries.append((match.group(1), match.group(2), number))

    headings = [
        (match.group(1).strip(), number)
        for number, line in enumerate(text.splitlines(), start=1)
        if (match := re.match(r"^## (?!#)(.+)$", line))
    ]

    if len(entries) != len(headings):
        findings.append(
            Finding(str(path), 0, f"{len(entries)} TOC entries but {len(headings)} level-2 headings")
        )

    for index, (label, anchor, line) in enumerate(entries):
        if index >= len(headings):
            findings.append(Finding(str(path), line, f"TOC entry [{label}] has no matching heading"))
            continue
        heading, heading_line = headings[index]
        if label != heading:
            findings.append(
                Finding(str(path), line, f"label [{label}] does not match heading [{heading}] at line {heading_line}")
            )
        expected = heading_slug(heading)
        if anchor != expected:
            findings.append(Finding(str(path), line, f"anchor #{anchor} does not resolve; expected #{expected}"))

    for index in range(len(entries), len(headings)):
        heading, heading_line = headings[index]
        findings.append(Finding(str(path), heading_line, f"heading [{heading}] is missing from the TOC"))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help=f"files to check (default: {' '.join(DEFAULT_FILES)})")
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    names = args.files or list(DEFAULT_FILES)
    findings: list[Finding] = []
    for name in names:
        findings += check(args.root / name)

    if args.json:
        print(json.dumps([asdict(finding) for finding in findings], indent=2))
    else:
        for finding in findings:
            print(f"FAIL  {finding.path}:{finding.line}  {finding.message}")
        if findings:
            print(f"\n{len(findings)} problem(s).")
        else:
            print(f"Table of contents resolves in {len(names)} file(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
