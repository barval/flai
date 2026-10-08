#!/usr/bin/env python3
"""Regenerate the table of contents between the TOC markers in the readmes.

Usage:
    python3 scripts/update_toc.py            # README.md and README-ru.md
    python3 scripts/update_toc.py path.md    # explicit files
"""

import pathlib
import re
import sys

BEGIN = "<!-- TOC:BEGIN -->"
END = "<!-- TOC:END -->"
ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_FILES = ("README.md", "README-ru.md")


def slug(text: str) -> str:
    """Approximate the GitHub heading anchor for a heading text."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_~]", "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "-", text)


def entries(lines: list[str]) -> list[tuple[str, str]]:
    out, fence = [], False
    for line in lines:
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence or not line.startswith("## ") or line.startswith("### "):
            continue
        out.append((line[3:].strip(), slug(line[3:])))
    return out


def update(path: pathlib.Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        print(f"{path}: markers not found, skipped")
        return True
    before, rest = text.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    body = "\n".join(f"- [{title}](#{anchor})" for title, anchor in entries(text.split("\n")))
    new = f"{before}{BEGIN}\n{body}\n{END}{after}"
    if new == text:
        print(f"{path}: up to date")
        return True
    path.write_text(new, encoding="utf-8")
    print(f"{path}: updated")
    return True


def main(argv: list[str]) -> int:
    files = argv[1:] or list(DEFAULT_FILES)
    ok = all(update(ROOT / name) for name in files)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
