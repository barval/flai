#!/usr/bin/env python3
"""Compare the heading structure of English and Russian FLAI documents.

Read-only. Run from the repository root.

Every ``docs/X.md`` must have a ``docs/X-ru.md`` twin with the same sequence of
heading levels. ``README.md`` and ``CONTRIBUTING.md`` are checked the same way.
Headings inside fenced code blocks are ignored.

Exit codes:
    0  every pair matches
    1  at least one missing twin or heading mismatch
    2  usage error
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Mismatch:
    english: str
    russian: str
    reason: str


def heading_levels(path: Path) -> list[int]:
    levels: list[int] = []
    fence: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if fence:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            continue
        match = re.match(r"^(#{1,6})\s+\S", line)
        if match:
            levels.append(len(match.group(1)))
    return levels


def compare(english: Path, russian: Path) -> Mismatch | None:
    if not russian.exists():
        return Mismatch(str(english), str(russian), "missing Russian twin")
    en_levels = heading_levels(english)
    ru_levels = heading_levels(russian)
    if en_levels == ru_levels:
        return None

    limit = min(len(en_levels), len(ru_levels))
    index = next((i for i in range(limit) if en_levels[i] != ru_levels[i]), limit)
    if index < limit:
        reason = f"heading {index + 1}: en level {en_levels[index]} != ru level {ru_levels[index]}"
    else:
        reason = f"heading count: en {len(en_levels)} != ru {len(ru_levels)}"
    return Mismatch(str(english), str(russian), reason)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    args = parser.parse_args()

    pairs: list[tuple[Path, Path]] = []
    for name in ("README.md", "CONTRIBUTING.md"):
        english = args.root / name
        if english.exists():
            pairs.append((english, args.root / name.replace(".md", "-ru.md")))
    for english in sorted((args.root / "docs").glob("*.md")):
        if english.name.endswith("-ru.md"):
            continue
        pairs.append((english, english.with_name(f"{english.stem}-ru.md")))

    mismatches = [mismatch for english, russian in pairs if (mismatch := compare(english, russian))]
    if mismatches:
        for mismatch in mismatches:
            print(f"{mismatch.english} <-> {mismatch.russian}: {mismatch.reason}")
        return 1

    print(f"Documentation headings match in {len(pairs)} pairs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
