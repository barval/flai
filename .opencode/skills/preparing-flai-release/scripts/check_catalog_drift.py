#!/usr/bin/env python3
"""Compare the strings found in the source tree with the message catalogs.

Read-only. Run from the repository root.

``check_translations.py`` proves the two ``.po`` catalogs agree with each other
and with their ``.mo`` files. It cannot see a string that exists in the code and
in neither catalog: such a string silently reaches the user in its source
language. This script extracts the source tree the same way ``pybabel`` does and
reports:

* ``untranslated`` - a message present in the code and absent from a catalog.
  A user-facing string here is a defect: the UI shows the source text.
* ``dead`` - a catalog entry no longer present in the code. Informational: it is
  what ``pybabel update`` turns into an obsolete entry.
* ``structure`` - a raw-format defect Babel tolerates silently, such as two
  ``msgstr`` lines in one entry (Babel keeps the last one) or an unescaped
  quote inside a message. Both were produced by hand-editing a catalog block.

Exit codes:
    0  no untranslated strings and no structural defects
    1  at least one finding
    2  usage error or a missing dependency
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from babel.messages.pofile import read_po
except ImportError:  # pragma: no cover - Babel is a Flask-Babel dependency
    print("error: Babel is required (install the project dependencies)", file=sys.stderr)
    raise SystemExit(2) from None

EXTRACT_KEYS = ("_tr", "ApiImageRejectedError", "ApiTaskError")
CATALOGS = ("en", "ru")


def load_ids(path: Path) -> set[tuple[str, str]]:
    """Return ``(context, msgid)`` for every live entry of a catalog or template."""
    with path.open("rb") as handle:
        catalog = read_po(handle)
    return {
        (message.context or "", message.id) for message in catalog if message.id and not getattr(message, "obsolete", False)
    }


def extract_pot(root: Path, pot: Path) -> None:
    """Write a fresh POT for the tree, using the same keys babel.cfg is read with."""
    command = [sys.executable, "-m", "babel.messages.frontend", "extract", "-F", "babel.cfg"]
    for key in EXTRACT_KEYS:
        command += ["-k", key]
    command += ["-o", str(pot), "."]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not pot.exists():
        print(f"error: pybabel extract failed\n{result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)


def _unescaped_quote(value: str) -> bool:
    """True when a quoted PO string carries a quote that is not escaped."""
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return False
    escaped = False
    for char in value[1:-1]:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            return True
    return False


def _entry_blocks(path: Path) -> list[tuple[int, list[str]]]:
    """Split a catalog into ``(first line number, lines)`` blocks."""
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 1
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            if current:
                blocks.append((start, current))
                current = []
            start = number + 1
            continue
        if not current:
            start = number
        current.append(line)
    if current:
        blocks.append((start, current))
    return blocks


def structure_issues(path: Path) -> list[str]:
    """Report raw-format defects that Babel parses without complaining."""
    issues: list[str] = []
    seen: dict[str, int] = {}
    for start, lines in _entry_blocks(path):
        msgids: list[tuple[int, str]] = []
        msgid_starts: list[int] = []
        msgstrs: list[tuple[int, str]] = []
        in_msgid = False
        for offset, line in enumerate(lines):
            number = start + offset
            if line.startswith("msgid "):
                msgid_starts.append(number)
                msgids.append((number, line[6:]))
                in_msgid = True
            elif line.startswith("msgstr "):
                msgstrs.append((number, line[7:]))
                in_msgid = False
            elif line.startswith('"') and in_msgid:
                msgids.append((number, line))
            if line.startswith(("msgid ", "msgstr ")) and _unescaped_quote(line.split(" ", 1)[1]):
                issues.append(f"{path}:{number}: unescaped quote inside {line.split(' ', 1)[0]}")
            elif line.startswith('"') and _unescaped_quote(line):
                issues.append(f"{path}:{number}: unescaped quote inside a continuation line")
        if len(msgid_starts) > 1:
            issues.append(f"{path}:{msgid_starts[1]}: {len(msgid_starts)} msgid lines in one entry")
        if len(msgstrs) > 1:
            issues.append(
                f"{path}:{msgstrs[1][0]}: {len(msgstrs)} msgstr lines in one entry (Babel silently keeps the last)"
            )
        if msgids:
            key = "".join(part for _, part in msgids)
            if key in seen:
                issues.append(f"{path}:{msgids[0][0]}: duplicate entry, first seen at line {seen[key]}")
            else:
                seen[key] = msgids[0][0]
    return issues


def display(key: tuple[str, str]) -> str:
    context, message_id = key
    text = str(message_id).replace("\n", " ")
    return f"{context}: {text}" if context else text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_catalog_drift.py", description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--max-items", type=int, default=40, help="findings printed per section (default: 40)")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / "babel.cfg").exists():
        print(f"error: {root} has no babel.cfg - run from the repository root", file=sys.stderr)
        return 2

    report: dict[str, Any] = {"untranslated": {}, "dead": {}, "structure": {}}
    with tempfile.TemporaryDirectory() as tmp:
        pot = Path(tmp) / "messages.pot"
        extract_pot(root, pot)
        pot_ids = load_ids(pot)
        report["source_messages"] = len(pot_ids)
        for language in CATALOGS:
            catalog_path = root / "translations" / language / "LC_MESSAGES" / "messages.po"
            if not catalog_path.exists():
                print(f"error: missing {catalog_path}", file=sys.stderr)
                return 2
            catalog_ids = load_ids(catalog_path)
            report["untranslated"][language] = sorted(display(key) for key in pot_ids - catalog_ids)
            report["dead"][language] = sorted(display(key) for key in catalog_ids - pot_ids)
            report["structure"][language] = structure_issues(catalog_path)

    untranslated = {lang: items for lang, items in report["untranslated"].items() if items}
    structure = {lang: items for lang, items in report["structure"].items() if items}
    dead = {lang: items for lang, items in report["dead"].items() if items}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"source messages: {report['source_messages']}")
        for language, items in untranslated.items():
            print(f"\n{language}: {len(items)} string(s) in the code, missing from the catalog")
            for item in items[: args.max_items]:
                print(f"  - {item[:150]}")
            if len(items) > args.max_items:
                print(f"  ... and {len(items) - args.max_items} more")
        for language, items in structure.items():
            print(f"\n{language}: {len(items)} structural defect(s)")
            for item in items[: args.max_items]:
                print(f"  ! {item}")
        for language, items in dead.items():
            print(f"\n{language}: {len(items)} obsolete entr(y/ies) no longer in the code (informational)")
            for item in items[: args.max_items]:
                print(f"  ? {item[:120]}")

    if untranslated or structure:
        if not args.json:
            print("\nFAIL: run pybabel extract/update, review every fuzzy block, then pybabel compile")
        return 1
    if not args.json:
        print("\nCatalog and source agree, and both catalogs are well formed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
