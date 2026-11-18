#!/usr/bin/env python3
"""Check the strings a release stamps on itself, in one place.

The version lives in seven places that no single test reads: ``APP_VERSION``,
``pyproject.toml``, the ``AGENTS.md`` title, both READMEs' What's New header,
the ``Project-Id-Version`` of both catalogs, and the ``CHANGELOG.md`` heading.
The workflow greps them by hand, which is the same as not checking them.

This script reports every disagreement, plus the two leftovers that ship by
accident: ``Unreleased`` anywhere in a release README, and an ``Unreleased``
CHANGELOG heading when ``--released`` says the release is being signed off.

Exit codes:
    0  consistent
    1  at least one finding
    2  usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _first(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.M)
    return match.group(1).strip() if match else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="expected X.Y (default: APP_VERSION from app/config.py)")
    parser.add_argument("--released", action="store_true", help="the date must be real, not `Unreleased`")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    config = root / "app" / "config.py"
    if not config.exists():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    config_text = config.read_text(encoding="utf-8")
    version = args.version or _first(r'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+)"', config_text)
    if not version:
        print("APP_VERSION not found in app/config.py", file=sys.stderr)
        return 2

    findings: list[tuple[str, str]] = []

    def expect(name: str, actual: str, wanted: str) -> None:
        if actual != wanted:
            findings.append((name, f"{actual or '<missing>'} != {wanted}"))

    major, minor = version.split(".")[:2]
    expect("app/config.py APP_VERSION", version, version)

    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    py_version = _first(r'^version\s*=\s*"([^"]+)"', pyproject)
    if not py_version.startswith(f"{major}.{minor}."):
        findings.append(("pyproject.toml version", f"{py_version or '<missing>'} != {version}.Z"))

    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    title = _first(r"^# AGENTS\.md — FLAI (v[0-9.]+)", agents)
    expect("AGENTS.md title", title, f"v{version}")

    for readme_name in ("README.md", "README-ru.md"):
        readme = (root / readme_name).read_text(encoding="utf-8")
        # The heading is English in one README and Russian in the other; both name the version.
        match = re.search(r"^#+\s+.*?(?:What's New in|Что нового в)\s+(v[0-9]+\.[0-9]+)", readme, re.M)
        expect(f"{readme_name} What's New", match.group(1) if match else "", f"v{version}")
        if "Unreleased" in readme:
            findings.append((readme_name, "still says `Unreleased`"))

    for po_name in ("translations/en/LC_MESSAGES/messages.po", "translations/ru/LC_MESSAGES/messages.po"):
        po = root / po_name
        if not po.exists():
            findings.append((po_name, "missing"))
            continue
        po_version = _first(
            r'"Project-Id-Version:\s*[^"\n]*?([0-9]+\.[0-9]+)(?:\\n)?"', po.read_text(encoding="utf-8")
        )
        expect(f"{po_name} Project-Id-Version", po_version, version)

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = _first(rf"^## \[v{re.escape(version)}\] — (.+)$", changelog)
    if not heading:
        findings.append(("CHANGELOG.md", f"no `## [v{version}] — <date>` heading"))
    elif args.released and heading.strip().lower() == "unreleased":
        findings.append(("CHANGELOG.md", "heading still says `Unreleased`"))

    if args.json:
        print(json.dumps([{"where": w, "problem": p} for w, p in findings], ensure_ascii=False, indent=1))
    else:
        for where, problem in findings:
            print(f"MISMATCH  {where}: {problem}")
        print(f"\n{len(findings)} release string finding(s) for v{version}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
