#!/usr/bin/env python3
"""Check documented behaviour against the call that implements it.

Paths, commands and keys are checked mechanically elsewhere. The harder claim is
behavioural: "the tool queues indexing", "an upload is indexed on arrival",
"a run never publishes a result event". Those sentences read perfectly while the
call behind them is gone, and the release then ships documentation that tells a
user something the code does not do.

Measured: `AGENTS.md` said `write_document` "queues the existing index_document
task, so the file is indexed and graphed exactly like an upload". It did not and
does not — the panel's index switch is the only path — and the claim survived a
whole release because nothing read it against the code.

This script keeps a registry of such claims: a phrase that must exist in a
document, and the code that must (or must not) contain the call it promises.
A claim whose phrase disappeared is reported too — a reworded sentence leaves the
registry pointing at text nobody reads any more.

It also lists, without judging, the sentences that contain the words a behaviour
claim is usually made of. That list is a reading task for the release reviewer,
not a gate.

Exit codes:
    0  every registered claim matches the code
    1  at least one claim contradicts the code, or lost its anchor
    2  usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Words a sentence uses when it promises behaviour rather than naming a file.
CLAIM_WORDS = re.compile(
    r"\b(automatically|immediately|on arrival|in the background|queues?|starts?|triggers?|"
    r"indexe[sd]?|syncs?|publishes?|creates?|uploads?|scans?|sends?|writes?|stores?)\b",
    re.I,
)


@dataclass(frozen=True)
class Claim:
    """One sentence, one call. The doc must say it; the code must do it."""

    id: str
    doc: str
    doc_pattern: str
    code: str
    code_must: str
    code_must_not: str = ""
    note: str = ""


CLAIMS: tuple[Claim, ...] = (
    Claim(
        id="write-document-no-auto-index",
        doc="AGENTS.md",
        doc_pattern=r"does \*\*not\*\* index it",
        code="app/tools.py",
        code_must=r"def _exec_write_document",
        code_must_not=r"index_document",
        note="a generated file is indexed by the panel switch, never by the write call",
    ),
    Claim(
        id="upload-indexes-on-arrival",
        doc="docs/DOCUMENTS.md",
        doc_pattern=r"indexed on arrival",
        code="app/routes/documents.py",
        code_must=r'"index_document"',
        note="an upload queues indexing; this is the difference the write path is measured against",
    ),
    Claim(
        id="restart-cancels-a-running-run",
        doc="docs/SCHEDULER.md",
        doc_pattern=r"A restart cannot wedge an item",
        code="app/scheduler_runs.py",
        code_must=r"def recover_after_restart",
        note="the documented restart recovery is the call the module exports",
    ),
    Claim(
        id="start-task-only-in-a-run",
        doc="AGENTS.md",
        doc_pattern=r"scheduler_run",
        code="app/tools.py",
        code_must=r"SCHEDULER_RUN_EXCLUDED_TOOLS",
        note="chat is never offered start_task; the run catalogue adds it",
    ),
    Claim(
        id="container-formats-are-rendered",
        doc="docs/DOCUMENTS.md",
        doc_pattern=r"\| Containers \|",
        code="app/documents_write.py",
        code_must=r"BINARY_FORMATS = \{",
        note="the documented .docx/.xlsx/.pdf set is the binary branch of the write path",
    ),
    Claim(
        id="run-round-ceiling-comes-from-config",
        doc="AGENTS.md",
        doc_pattern=r"SCHEDULER_RUN_ROUNDS",
        code="app/scheduler_runner.py",
        code_must=r"SCHEDULER_RUN_ROUNDS",
        note="the documented ceiling is the value the runner reads",
    ),
)

@dataclass(frozen=True)
class DocClaim:
    """A shipped feature may not contradict another document.

    These are not doc-vs-code claims: the feature is in the code and in the
    README, and the failure is another page that still describes it as planned,
    or the same figure written two ways. v13.6 shipped music while ROADMAP.md
    still planned it, and MUSIC.md called the same image "~9 GB" and "20.8 GB".
    """

    id: str
    anchor_doc: str
    anchor_pattern: str
    forbidden_doc: str
    forbidden_pattern: str


DOC_CLAIMS: tuple[DocClaim, ...] = (
    DocClaim(
        "music-not-planned",
        "README.md",
        r"writes songs",
        "docs/ROADMAP.md",
        r"Music generation and sung songs",
    ),
    DocClaim(
        "music-not-planned-ru",
        "README-ru.md",
        r"пишет песни",
        "docs/ROADMAP-ru.md",
        r"Генерация музыки и озвученных песен",
    ),
    DocClaim(
        "music-image-size",
        "docs/MUSIC.md",
        r"20\.8 GB on disk",
        "docs/MUSIC.md",
        r"~9 GB",
    ),
    DocClaim(
        "music-image-size-ru",
        "docs/MUSIC-ru.md",
        r"20,8 ГБ на диске",
        "docs/MUSIC-ru.md",
        r"~9 ГБ",
    ),
)


def _check_doc_claims(root: Path) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    for claim in DOC_CLAIMS:
        anchor = _read(root, claim.anchor_doc)
        forbidden = _read(root, claim.forbidden_doc)
        if not re.search(claim.anchor_pattern, anchor):
            findings.append(
                (claim.id, "anchor_lost", f"{claim.anchor_doc} no longer contains {claim.anchor_pattern!r}")
            )
            continue
        if re.search(claim.forbidden_pattern, forbidden):
            findings.append(
                (
                    claim.id,
                    "contradicted",
                    f"{claim.forbidden_doc} still contains {claim.forbidden_pattern!r}, which the shipped feature denies",
                )
            )
    return findings




def _read(root: Path, name: str) -> str:
    path = root / name
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def _behaviour_sentences(root: Path) -> list[tuple[str, int, str]]:
    """Sentences a reviewer should read against the call, not a verdict."""
    found: list[tuple[str, int, str]] = []
    documents = [root / "AGENTS.md", root / "README.md"]
    documents += sorted((root / "docs").glob("*.md"))
    for document in documents:
        if not document.exists() or document.stem.endswith("-ru"):
            continue
        for number, line in enumerate(document.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            text = line.strip()
            if len(text) < 40 or text.startswith(("|", "#", "```")):
                continue
            if CLAIM_WORDS.search(text):
                found.append((str(document.relative_to(root)), number, text[:180]))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--list", action="store_true", help="print every behaviour-shaped sentence for a human to check"
    )
    parser.add_argument("--max-list", type=int, default=40, help="how many sentences --list prints (default 40)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    if not (root / "AGENTS.md").exists():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    findings: list[tuple[str, str, str]] = []  # (id, kind, detail)
    for claim in CLAIMS:
        doc_text = _read(root, claim.doc)
        code_text = _read(root, claim.code)
        if not re.search(claim.doc_pattern, doc_text):
            findings.append((claim.id, "anchor_lost", f"{claim.doc} no longer contains {claim.doc_pattern!r}"))
            continue
        if not re.search(claim.code_must, code_text):
            findings.append((claim.id, "missing_call", f"{claim.code} does not contain {claim.code_must!r}"))
        if claim.code_must_not and re.search(claim.code_must_not, code_text):
            findings.append(
                (claim.id, "contradicted", f"{claim.code} contains {claim.code_must_not!r}, which the document denies")
            )

    findings.extend(_check_doc_claims(root))

    listed = _behaviour_sentences(root) if args.list else []

    if args.json:
        print(
            json.dumps(
                {
                    "findings": [{"id": i, "kind": k, "detail": d} for i, k, d in findings],
                    "listed": [{"file": f, "line": n, "text": t} for f, n, t in listed],
                },
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        for claim_id, kind, detail in findings:
            print(f"{kind.upper():13} {claim_id}: {detail}")
        if args.list:
            print(f"\n{len(listed)} behaviour-shaped sentence(s) to read against the code:")
            for file_name, number, text in listed[: args.max_list]:
                print(f"  {file_name}:{number}  {text}")
        print(f"\n{len(findings)} claim(s) out of step, {len(CLAIMS) + len(DOC_CLAIMS)} registered")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
