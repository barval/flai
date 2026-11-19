#!/usr/bin/env python3
"""Every deployment key must exist in the script, the usage text and the agent skill.

A key is a promise in three places at once: `deploy.sh` parses it, the usage block
describes it, and `.opencode/skills/deploying-flai/SKILL.md` tells an agent to run it.
The three drifted exactly as this predicts — `--with-search` and `--with-crawler` were
implemented and missing from the usage text, `--with-net-tools` was missing from the
Russian usage and from the agent skill, and the Russian `--with-net-tools` block lost
its indentation.

This script compares the three inventories and reports:

* a key the scripts parse but the usage does not describe;
* a key the usage describes but the scripts do not parse;
* a key one language implements and the other does not;
* a key the agent skill never names (an agent then cannot choose it);
* a key the agent skill names that no script parses.

`--platform=NAME` is normalised to `--platform`; `-h` is normalised to `--help`.

Exit codes:
    0  the three inventories agree
    1  at least one finding
    2  usage or file error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS = ("deploy.sh", "deploy-ru.sh")
SKILL = ".opencode/skills/deploying-flai/SKILL.md"
FLAG = re.compile(r"--[a-z0-9][a-z0-9-]*")
CASE_BLOCK = re.compile(r'case "\$arg" in(.*?)\n\s*esac', re.S)
USAGE_BLOCK = re.compile(r"<<'USAGE'\n(.*?)\nUSAGE\b", re.S)


def _normalise(flag: str) -> str:
    return "--help" if flag in {"-h", "--help"} else flag


def _case_flags(root: Path, name: str) -> set[str]:
    text = (root / name).read_text(encoding="utf-8")
    block = CASE_BLOCK.search(text)
    if block is None:
        return set()
    # `--platform=*` and `--platform=NAME` are the same key.
    return {_normalise(match) for match in FLAG.findall(block.group(1).replace("=*", ""))}


def _usage_flags(root: Path, name: str) -> set[str]:
    text = (root / name).read_text(encoding="utf-8")
    block = USAGE_BLOCK.search(text)
    if block is None:
        return set()
    return {_normalise(match) for match in FLAG.findall(block.group(1))}


def _skill_flags(root: Path, known: set[str]) -> set[str]:
    """Flags the skill names, limited to the deployment namespace.

    The skill also quotes `git --ff-only`, `docker --format` and `nvidia-smi --query-gpu`;
    those are other tools. A `--with-…` name is always treated as a deployment key so an
    invented one is caught.
    """
    text = (root / SKILL).read_text(encoding="utf-8")
    return {
        _normalise(match)
        for match in FLAG.findall(text)
        if match.startswith("--with-") or match in known
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    if not (root / "deploy.sh").exists():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    implemented = {name: _case_flags(root, name) for name in SCRIPTS}
    documented = {name: _usage_flags(root, name) for name in SCRIPTS}
    known = set().union(*implemented.values(), *documented.values())
    in_skill = _skill_flags(root, known) if (root / SKILL).exists() else set()

    findings: list[tuple[str, str, str]] = []  # kind, where, detail

    for name in SCRIPTS:
        for flag in sorted(implemented[name] - documented[name]):
            findings.append(("missing_in_usage", name, flag))
        for flag in sorted(documented[name] - implemented[name]):
            findings.append(("missing_in_code", name, flag))

    english, russian = (implemented[name] for name in SCRIPTS)
    for flag in sorted(english ^ russian):
        findings.append(("language_asymmetry", f"{SCRIPTS[0]} / {SCRIPTS[1]}", flag))

    for flag in sorted(known - in_skill):
        findings.append(("skill_missing", SKILL, flag))
    for flag in sorted(in_skill - known):
        findings.append(("skill_unknown", SKILL, flag))

    if args.json:
        print(
            json.dumps(
                [{"kind": k, "where": w, "flag": f} for k, w, f in findings],
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        for kind, where, flag in findings:
            print(f"{kind.upper():20} {where:34} {flag}")
        print(
            f"\n{len(findings)} deploy-key finding(s); "
            f"{len(known)} key(s) across {len(SCRIPTS)} scripts and the agent skill"
        )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
