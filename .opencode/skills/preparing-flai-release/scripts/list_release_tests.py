#!/usr/bin/env python3
"""Name the test files a release must run, so the choice is not made from memory.

The release gate runs a fixed short list, and that list was written when the
project was smaller: two tests had been red since v13.5 and no gate ever touched
them. This script reads the release diff and answers one question — which test
files exercise the code this release changed?

Files changed under ``app/`` or ``modules/`` are matched by the tests that import
them (``from app.queue import``, ``import app.tasks.store``, or the module's own
name for a package). Changed tests are included as they are.

It never runs anything: the release workflow runs one file at a time, and a
background suite is forbidden by the project's memory rules. Print the command,
run it deliberately.

Exit codes:
    0  a list was produced
    1  code changed and no test covers it (a rule, not a bug in this script)
    2  usage or git error
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SOURCE_ROOTS = ("app/", "modules/")


def _git(root: Path, *args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _module_names(path: str) -> set[str]:
    """The import spellings a test would use for this file."""
    dotted = path[: -len(".py")].replace("/", ".")
    names = {dotted}
    if dotted.endswith(".__init__"):
        names.add(dotted[: -len(".__init__")])
    return names


def _tests_importing(tests: list[Path], modules: set[str]) -> set[Path]:
    """A test that imports the module or patches one of its names.

    A bare text match is useless here: ``app.queue`` is imported by half the suite,
    and ``app/__init__.py`` reduces to ``app``, which matches everything. Two
    shapes count — an import of the module, and a string reference to a name
    inside it (``patch("app.queue.RedisRequestQueue")``).
    """
    hits: set[Path] = set()
    for test in tests:
        try:
            text = test.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for module in modules:
            pattern = (
                rf"(?:^|\n)\s*from\s+{re.escape(module)}\s+import\b"
                rf"|(?:^|\n)\s*import\s+{re.escape(module)}\b"
                rf"|[\"']{re.escape(module)}\.\w+"
            )
            if re.search(pattern, text):
                hits.add(test)
                break
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="previous release, e.g. v13.4")
    parser.add_argument("--head", default="HEAD", help="release tip (default: HEAD)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--broad-threshold",
        type=int,
        default=15,
        help="a module imported by more tests than this is infrastructure, not a subject (default: 15)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    if not (root / "tests").is_dir():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    try:
        changed = _git(root, "diff", "--name-only", f"{args.base}..{args.head}")
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    source_changed = [p for p in changed if p.startswith(SOURCE_ROOTS) and p.endswith(".py")]
    tests_changed = [p for p in changed if p.startswith("tests/") and p.endswith(".py")]
    modules: set[str] = set()
    for path in source_changed:
        modules |= _module_names(path)

    all_tests = sorted((root / "tests").glob("test_*.py"))
    importer_map = {module: _tests_importing(all_tests, {module}) for module in sorted(modules)}
    broad = {m for m, hits in importer_map.items() if len(hits) > args.broad_threshold}
    narrow = set(modules) - broad
    covering: set[Path] = set()
    for module in narrow:
        covering |= importer_map[module]
    # A test named after the subsystem is a subject even when the module itself is
    # imported everywhere: `app/scheduler.py` is imported by the whole queue area, and
    # `test_scheduler_*.py` is still the set that proves it works.
    by_name = {
        test
        for test in all_tests
        if any(re.search(rf"\b{re.escape(Path(m).name)}\b", test.stem) for m in narrow)
    }
    selected = sorted({root / p for p in tests_changed} | covering | by_name)
    relative = [str(p.relative_to(root)) for p in selected]

    if args.json:
        print(
            json.dumps(
                {
                    "changed_sources": source_changed,
                    "changed_tests": tests_changed,
                    "selected": relative,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        print(f"source files changed: {len(source_changed)}")
        for path in source_changed:
            print(f"  {path}")
        if broad:
            print(
                f"\nnot used as a selection signal (imported by more than {args.broad_threshold} tests): "
                + ", ".join(sorted(broad))
            )
        print(f"\ntests to run ({len(relative)}):")
        for path in relative:
            print(f"  {path}")
        if relative:
            print("\nRun them one at a time, with the test database exported in the same command:")
            print('  export DATABASE_URL="postgresql://flai:flai_password@127.0.0.1:5432/flai_test"')
            print('  for f in \\\n    ' + " \\\n    ".join(relative) + "; do")
            print('    printf "%-34s " "$f"; timeout 600 .venv/bin/python -m pytest "$f" -q 2>&1 | tail -1')
            print("  done")
        if source_changed and not relative:
            print("\nNo test mentions the changed modules: add one before the release.")

    return 1 if source_changed and not relative else 0


if __name__ == "__main__":
    sys.exit(main())
