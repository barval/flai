#!/usr/bin/env python3
"""Check that a release's schema changes are safe and written down.

``app/database.py:init_db()`` runs on every start, so a statement in it is not a
one-time migration: it is repeated for the life of the installation. A ``DROP
COLUMN`` deletes data the moment the new code starts, and nothing in the test
suite can see it — the tests build their own schema.

This script reads the release diff of ``app/database.py`` and reports every
destructive or narrowing statement. Each affected object must be named in a
``### Migration`` section of ``CHANGELOG.md``; that section is the only place a
person upgrading learns what a column drop costs.

It does not touch a database. Where a statement needs a data check, it prints a
ready command to run against a dump (the release workflow takes the backup).

Exit codes:
    0  no destructive statement, or every one is documented
    1  at least one finding
    2  usage or git error
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# (pattern, what it does to existing data)
DANGEROUS = (
    (re.compile(r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?([a-z_][a-z0-9_]*)", re.I), "drops a column and its data"),
    (re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([a-z_][a-z0-9_]*)", re.I), "drops a table and its rows"),
    (re.compile(r"TRUNCATE\s+(?:TABLE\s+)?([a-z_][a-z0-9_]*)", re.I), "deletes every row"),
    (re.compile(r"RENAME\s+(?:COLUMN|TO)\s+([a-z_][a-z0-9_]*)", re.I), "renames an object old code still reads"),
    (re.compile(r"ALTER\s+COLUMN\s+([a-z_][a-z0-9_]*)\s+(?:TYPE|SET\s+NOT\s+NULL|SET\s+DATA\s+TYPE)", re.I),
     "rewrites or narrows existing values"),
    (re.compile(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)[^)]*\bNOT\s+NULL\b(?!\s+DEFAULT)", re.I),
     "adds a NOT NULL column without a default; existing rows fail"),
)


@dataclass(frozen=True)
class Finding:
    object_name: str
    effect: str
    statement: str
    documented: bool


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _added_lines(patch: str) -> list[str]:
    """Only what this release adds: context lines describe code that already ran."""
    return [
        line[1:].strip()
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
    ]


def _migration_section(changelog: Path) -> str:
    text = changelog.read_text(encoding="utf-8") if changelog.exists() else ""
    match = re.search(r"^### Migration\s*$(.*?)(?=^### |^## |\Z)", text, re.M | re.S)
    return match.group(1) if match else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="previous release, e.g. v13.4")
    parser.add_argument("--head", default="HEAD", help="release tip (default: HEAD)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    if not (root / "app" / "database.py").exists():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    try:
        patch = _git(root, "diff", f"{args.base}..{args.head}", "--", "app/database.py")
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    documented_text = _migration_section(root / "CHANGELOG.md")
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()

    for line in _added_lines(patch):
        for pattern, effect in DANGEROUS:
            match = pattern.search(line)
            if not match:
                continue
            name = match.group(1).lower()
            key = (name, effect)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    object_name=name,
                    effect=effect,
                    statement=line[:160],
                    documented=bool(re.search(rf"\b{re.escape(name)}\b", documented_text, re.I)),
                )
            )
            break

    missing = [f for f in findings if not f.documented]

    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=1))
    else:
        for finding in findings:
            status = "documented" if finding.documented else "UNDOCUMENTED"
            print(f"{status:12} {finding.object_name:28} {finding.effect}")
            print(f"             {finding.statement}")
        if findings:
            print(
                "\nEvery statement in `init_db()` runs on every start, not once. Before the release:\n"
                "take a backup, then check what the statement will destroy on a copy of the real\n"
                "schema:\n\n"
                "  docker exec flai-postgres pg_dump -U flai -d flai > backups/pre-work_$(date +%F_%H-%M-%S).sql\n"
                "  docker exec -i flai-postgres createdb -U flai flai_migration_check\n"
                "  docker exec -i flai-postgres psql -U flai -d flai_migration_check < backups/pre-work_*.sql\n"
                "  docker exec flai-postgres psql -U flai -d flai_migration_check -c \\\n"
                "    \"SELECT count(*) FILTER (WHERE <column> IS NOT NULL) AS affected FROM <table>\"\n\n"
                "Then run the application twice against that copy: a migration that is not idempotent\n"
                "fails on the second start, in front of the user.\n"
            )
        print(f"{len(missing)} undocumented destructive change(s)")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
