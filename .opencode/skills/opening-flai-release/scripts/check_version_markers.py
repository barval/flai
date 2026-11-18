#!/usr/bin/env python3
"""Check the current-version markers of an open FLAI release line.

Read-only. Run from the repository root after the version bump.

The *mandatory* checks describe what a new release line must look like: the
branch is cut, every place that prints the current version names the new one,
the previous CHANGELOG section is dated, the new one is ``Unreleased``, and
``What's New`` holds no release bullets yet.

Every remaining occurrence of the previous version is printed as *review*.
A review row is not an error: the version may legitimately survive in
historical annotations, in CHANGELOG history, or in an unrelated version
number (``CUDA_VERSION_SD="12.6.3"``). Read each row and classify it; never
rewrite one with a blind search-and-replace.

Exit codes:
    0  every mandatory marker is correct (review rows may exist)
    1  at least one mandatory marker is missing or stale
    2  usage or file error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

BINARY_SUFFIXES = {
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mo",
    ".pdf",
    ".png",
    ".log",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".opencode",
    ".superpowers",
    ".pytest_cache",
    ".ruff_cache",
    "backups",
    "build",
    "data",
    "dist",
    "espeak-data",
    "flai.egg-info",
    "graphify-out",
    "llama-swap-config",
    "logs",
    "models",
    "node_modules",
    "services",
    "superpowers",
    "uploads",
    "venv",
    ".venv",
    "__pycache__",
}

# Read guard: a file this large is a model, a dump or an archive, never a
# hand-edited marker. Reading it as text is what turns the scan into minutes.
MAX_REVIEW_BYTES = 1_000_000

SKIP_FILES = {
    # Generated and environment-local files that mirror tracked content.
    ".env",
}

DOC_TITLE_PAIRS = (
    "docs/ARCHITECTURE.md",
    "docs/LOCALIZATION.md",
    "docs/RELEASE_GUIDE.md",
    "docs/VRAM_MANAGEMENT.md",
)

# Historical annotations: a bare "(v12.6)" or "(v12.6 fix)" marks when a
# feature appeared and must survive the next bump.
HISTORICAL_RE = re.compile(r"\(\s*v?{prev}\b[^)]*\)")
PATCH_RE = re.compile(r"\b{prev}\.\d")
RANGE_RE = re.compile(r"\b{prev}\s*[-\u2013\u2014]\s*{version}\b")


@dataclass(frozen=True)
class Finding:
    kind: str  # "fail" | "review"
    path: str
    line: int
    message: str


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def lines(path: Path) -> list[str]:
    return read(path).splitlines()


def first_heading(path: Path) -> tuple[int, str] | None:
    for number, line in enumerate(lines(path), start=1):
        if line.startswith("# "):
            return number, line
    return None


def check_constant(root: Path, rel: str, pattern: str, expected: str, label: str) -> list[Finding]:
    path = root / rel
    if not path.exists():
        return [Finding("fail", rel, 0, f"{label}: file is missing")]
    for number, line in enumerate(lines(path), start=1):
        match = re.search(pattern, line)
        if match:
            if match.group(1) != expected:
                return [Finding("fail", rel, number, f"{label}: expected {expected}, found {match.group(1)}")]
            return []
    return [Finding("fail", rel, 0, f"{label}: marker not found")]


def check_doc_titles(root: Path, version: str) -> list[Finding]:
    findings: list[Finding] = []

    # Every tracked document that carries a version in its title must carry the
    # new one; a document without a version in its title is left alone.
    candidates = [root / name for name in DOC_TITLE_PAIRS]
    candidates += sorted((root / "docs").glob("*.md"))
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        heading = first_heading(path)
        if heading is None:
            continue
        number, text = heading
        match = re.search(r"v(\d+\.\d+)", text)
        if match and match.group(1) != version:
            findings.append(
                Finding("fail", str(path.relative_to(root)), number, f"doc title still names v{match.group(1)}")
            )

    agents = root / "AGENTS.md"
    if agents.exists():
        heading = first_heading(agents)
        if heading is None or f"v{version}" not in heading[1]:
            findings.append(Finding("fail", "AGENTS.md", heading[0] if heading else 0, "title must name the release"))
    return findings


def check_deploy_scripts(root: Path, version: str, previous: str) -> list[Finding]:
    findings: list[Finding] = []
    for name in ("deploy.sh", "deploy-ru.sh"):
        path = root / name
        if not path.exists():
            findings.append(Finding("fail", name, 0, "deployment script is missing"))
            continue
        current = 0
        for number, line in enumerate(lines(path), start=1):
            if f"v{previous}" in line:
                findings.append(Finding("fail", name, number, f"still names v{previous}"))
            if f"v{version}" in line:
                current += 1
        # Header, success message, usage function and usage echo.
        if current < 4:
            findings.append(Finding("fail", name, 0, f"expected 4 version lines, found {current}"))
    return findings


def check_translations(root: Path, version: str) -> list[Finding]:
    findings: list[Finding] = []
    for language in ("en", "ru"):
        rel = f"translations/{language}/LC_MESSAGES/messages.po"
        path = root / rel
        if not path.exists():
            findings.append(Finding("fail", rel, 0, "catalog is missing"))
            continue
        pattern = re.compile(r'"Project-Id-Version: FLAI (\d+\.\d+)')
        found = False
        for number, line in enumerate(lines(path), start=1):
            match = pattern.search(line)
            if match:
                found = True
                if match.group(1) != version:
                    findings.append(Finding("fail", rel, number, f"expected FLAI {version}, found FLAI {match.group(1)}"))
        if not found:
            findings.append(Finding("fail", rel, 0, "Project-Id-Version marker not found"))
        compiled = path.with_suffix(".mo")
        if not compiled.exists():
            findings.append(Finding("fail", rel, 0, "compiled .mo is missing (run pybabel compile)"))
    return findings


def check_readmes(root: Path, version: str, released: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    slug = "#-whats-new-in-v" + version.replace(".", "")
    russian_slug = "#-что-нового-в-v" + version.replace(".", "")
    for name, heading, anchor, stop in (
        ("README.md", f"## 🆕 What's New in v{version}", slug, "## "),
        ("README-ru.md", f"## 🆕 Что нового в v{version}", russian_slug, "## "),
    ):
        path = root / name
        if not path.exists():
            findings.append(Finding("fail", name, 0, "README is missing"))
            continue
        content = lines(path)
        if not any(line.strip() == heading for line in content):
            findings.append(Finding("fail", name, 0, f"missing heading: {heading}"))
        anchor_lines = [(number, line) for number, line in enumerate(content, start=1) if anchor in line]
        if not anchor_lines:
            findings.append(Finding("fail", name, 0, f"table of contents has no {anchor} entry"))
        elif not any(f"v{version}" in line for _, line in anchor_lines):
            # A stale label pointing at the right anchor still reads like the
            # old release to a reader; scripts/update_toc.py regenerates it.
            findings.append(
                Finding("fail", name, anchor_lines[0][0], f"table of contents label must name v{version}")
            )

        start = next((i for i, line in enumerate(content) if line.strip() == heading), None)
        if start is None:
            continue
        bullets = 0
        for number in range(start + 1, len(content)):
            stripped = content[number].strip()
            # `Full history:` sits directly under the heading, before the bullets.
            if stripped.startswith(stop):
                break
            if stripped.startswith("Full history:"):
                continue
            if stripped.startswith("- "):
                bullets += 1
                if not released:
                    findings.append(
                        Finding("fail", name, number + 1, "What's New must be empty when the release line opens")
                    )
                    break
        if released and bullets == 0:
            findings.append(Finding("fail", name, 0, "What's New is empty in a released tree"))
    return findings


DEMO_PAGES = (
    "docs/demo/admin-en.html",
    "docs/demo/admin-ru.html",
    "docs/demo/chat-en.html",
    "docs/demo/chat-ru.html",
)


def check_demo_pages(root: Path, version: str) -> list[Finding]:
    """The published demo pages print the release version in their footer.

    They are generated by ``python -m demo.build``, never hand-edited: the build
    stamps ``window.FLAI_VERSION`` and the footer/About dialog from
    ``APP_VERSION``, so a page that still names the previous release means the
    opening did not rebuild the artefacts.
    """
    findings: list[Finding] = []
    pattern = re.compile(r'FLAI_VERSION = "(\d+\.\d+)"')
    for name in DEMO_PAGES:
        path = root / name
        if not path.exists():
            findings.append(Finding("fail", name, 0, "demo page is missing"))
            continue
        found: tuple[int, str] | None = None
        for number, line in enumerate(lines(path), start=1):
            match = pattern.search(line)
            if match:
                found = (number, match.group(1))
                break
        if found is None:
            findings.append(Finding("fail", name, 0, "FLAI_VERSION marker not found"))
        elif found[1] != version:
            findings.append(
                Finding("fail", name, found[0], f"demo page still prints v{found[1]} (run python -m demo.build)")
            )
    return findings


def check_contributing(root: Path, version: str, previous: str) -> list[Finding]:
    findings: list[Finding] = []
    for name in ("CONTRIBUTING.md", "CONTRIBUTING-ru.md"):
        path = root / name
        if not path.exists():
            findings.append(Finding("fail", name, 0, "contributor guide is missing"))
            continue
        for number, line in enumerate(lines(path), start=1):
            # The "numbered main branch" line names a pair (v12.6, v12.7):
            # the previous branch is where contributors cut from until the
            # release ships, so both versions belong there.
            if f"v{previous}" in line and f"v{version}" not in line:
                findings.append(Finding("fail", name, number, f"branch example still names v{previous}"))
        text = read(path)
        if f"features/v{version}-" not in text or f"fix/v{version}-" not in text:
            findings.append(Finding("fail", name, 0, "branch naming examples must name the new release"))
    return findings


SECTION = re.compile(r"^## \[v(\d+\.\d+)\] — (.+?)\s*$")


def _sections(content: list[str]) -> list[tuple[int, str, str]]:
    """(line number, version, heading tail) for every release section, in file order."""
    found: list[tuple[int, str, str]] = []
    for number, line in enumerate(content, start=1):
        match = SECTION.match(line)
        if match:
            found.append((number, match.group(1), match.group(2).strip()))
    return found


def _empty_subsections(content: list[str], start: int) -> list[int]:
    """Lines of `### ` headings under this section that carry no entry.

    Opening writes the heading and nothing else; a subsection appears when it has
    content. An empty one is a promise the release never kept.
    """
    empty: list[int] = []
    current_heading: int | None = None
    has_entry = False
    for index in range(start, len(content)):
        line = content[index]
        if line.startswith("## ") and index > start:
            break
        if line.startswith("### "):
            if current_heading is not None and not has_entry:
                empty.append(current_heading)
            current_heading = index + 1
            has_entry = False
            continue
        if current_heading is not None and line.strip() and not line.startswith("####"):
            has_entry = True
    if current_heading is not None and not has_entry:
        empty.append(current_heading)
    return empty


def check_changelog(
    root: Path,
    version: str,
    previous: str,
    released: bool = False,
    source_date: str = "",
) -> list[Finding]:
    path = root / "CHANGELOG.md"
    if not path.exists():
        return [Finding("fail", "CHANGELOG.md", 0, "changelog is missing")]
    findings: list[Finding] = []
    content = lines(path)
    sections = _sections(content)
    wanted = "a date" if released else "Unreleased"

    current = [(number, tail) for number, name, tail in sections if name == version]
    if not current:
        findings.append(Finding("fail", "CHANGELOG.md", 0, f"missing section: ## [v{version}] — {wanted}"))
    else:
        number, tail = current[0]
        if released and tail.lower() == "unreleased":
            findings.append(Finding("fail", "CHANGELOG.md", number, f"v{version} must carry the release date"))
        if not released and tail.lower() != "unreleased":
            findings.append(Finding("fail", "CHANGELOG.md", number, f"v{version} must read `— Unreleased` while it is open"))
        if released:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tail):
                findings.append(Finding("fail", "CHANGELOG.md", number, f"v{version} needs an ISO date, found {tail!r}"))
            elif source_date and tail < source_date:
                findings.append(
                    Finding(
                        "fail",
                        "CHANGELOG.md",
                        number,
                        f"v{version} is dated {tail}, before the {previous} head ({source_date})",
                    )
                )
        for line_number in _empty_subsections(content, number):
            findings.append(Finding("fail", "CHANGELOG.md", line_number, "subsection has no entries"))

    names = [name for _number, name, _tail in sections]
    if version in names and previous in names and names.index(version) > names.index(previous):
        findings.append(
            Finding("fail", "CHANGELOG.md", 0, f"v{version} must appear above v{previous}")
        )
    # Anything above the checked release is a line opened after it: it may exist, but
    # it is not shipped yet, so it must still be Unreleased.
    for number, name, tail in sections:
        if name == version:
            break
        if tail.lower() != "unreleased":
            findings.append(
                Finding("fail", "CHANGELOG.md", number, f"v{name} sits above v{version} and is already dated")
            )

    previous_sections = [(number, tail) for number, name, tail in sections if name == previous]
    if not previous_sections:
        findings.append(Finding("fail", "CHANGELOG.md", 0, f"previous section v{previous} is missing"))
    else:
        number, tail = previous_sections[0]
        if tail.lower() == "unreleased":
            findings.append(Finding("fail", "CHANGELOG.md", number, f"v{previous} must be dated, not Unreleased"))
        elif source_date and tail != source_date:
            findings.append(
                Finding("fail", "CHANGELOG.md", number, f"v{previous} is dated {tail}; the SOURCE head is {source_date}")
            )
    return findings


def check_agents_branch_pair(root: Path, version: str, previous: str) -> list[Finding]:
    path = root / "AGENTS.md"
    if not path.exists():
        return []
    pattern = re.compile(r"numbered main project branch")
    for number, line in enumerate(lines(path), start=1):
        if pattern.search(line) and (f"v{version}" not in line or f"v{previous}" not in line):
            return [Finding("fail", "AGENTS.md", number, f"branch pair must read v{previous}, v{version}")]
    return []


def review_scan(root: Path, version: str, previous: str) -> list[Finding]:
    historical = re.compile(HISTORICAL_RE.pattern.format(prev=re.escape(previous)))
    patch = re.compile(PATCH_RE.pattern.format(prev=re.escape(previous)))
    interval = re.compile(RANGE_RE.pattern.format(prev=re.escape(previous), version=re.escape(version)))
    findings: list[Finding] = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for filename in sorted(filenames):
            if filename in SKIP_FILES:
                continue
            path = Path(directory) / filename
            if path.suffix in BINARY_SUFFIXES:
                continue
            try:
                if path.stat().st_size > MAX_REVIEW_BYTES:
                    continue
                text = read(path)
            except (UnicodeDecodeError, OSError):
                continue
            if previous not in text:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if previous not in line:
                    continue
                if path.name == "CHANGELOG.md" or historical.search(line) or patch.search(line) or interval.search(line):
                    continue
                if f"v{version}" in line:
                    # A branch pair ("v12.6, v12.7") legitimately names both.
                    continue
                findings.append(Finding("review", str(path.relative_to(root)), number, line.strip()[:160]))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="new release line, for example 12.7")
    parser.add_argument("--previous", required=True, help="source release line, for example 12.6")
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--released",
        action="store_true",
        help="the line is being signed off: the section carries a date and What's New is filled",
    )
    parser.add_argument(
        "--source-date",
        default="",
        help="date of the SOURCE head (YYYY-MM-DD); both CHANGELOG sections are compared with it",
    )
    parser.add_argument(
        "--no-branch-check",
        action="store_true",
        help="skip the warning about SOURCE not being merged into master",
    )
    args = parser.parse_args()

    version = args.version.strip().lstrip("v")
    previous = args.previous.strip().lstrip("v")
    if not re.fullmatch(r"\d+\.\d+", version) or not re.fullmatch(r"\d+\.\d+", previous):
        print("error: --version and --previous must look like 12.7", file=sys.stderr)
        return 2
    if version == previous:
        print("error: --version and --previous must differ", file=sys.stderr)
        return 2
    if not (args.root / "AGENTS.md").exists():
        print(f"error: {args.root} does not look like the FLAI repository root", file=sys.stderr)
        return 2

    warnings: list[Finding] = []
    if not args.no_branch_check and (args.root / ".git").exists():
        merged = subprocess.run(
            ["git", "merge-base", "--is-ancestor", f"v{previous}", "master"],
            cwd=args.root,
            capture_output=True,
        )
        if merged.returncode == 1:
            warnings.append(
                Finding(
                    "warn",
                    f"v{previous}",
                    0,
                    "not merged into master: open the new line only after the previous one ships, or say why",
                )
            )

    failures: list[Finding] = []
    failures += check_constant(args.root, "app/config.py", r'^APP_VERSION = "(\d+\.\d+)"', version, "APP_VERSION")
    failures += check_constant(args.root, "pyproject.toml", r'^version = "(\d+\.\d+\.\d+)"', f"{version}.0", "project version")
    failures += check_constant(
        args.root, "docs/openapi-v1.yaml", r"^\s+version:\s*(\d+\.\d+\.\d+)\s*$", f"{version}.0", "OpenAPI version"
    )
    failures += check_doc_titles(args.root, version)
    failures += check_deploy_scripts(args.root, version, previous)
    failures += check_translations(args.root, version)
    failures += check_readmes(args.root, version, args.released)
    failures += check_demo_pages(args.root, version)
    failures += check_contributing(args.root, version, previous)
    failures += check_changelog(args.root, version, previous, args.released, args.source_date)
    failures += check_agents_branch_pair(args.root, version, previous)
    reviews = review_scan(args.root, version, previous)
    covered = {(finding.path, finding.line) for finding in failures}
    reviews = [finding for finding in reviews if (finding.path, finding.line) not in covered]

    if args.json:
        print(
            json.dumps(
                {
                    "failures": [asdict(f) for f in failures],
                    "warnings": [asdict(f) for f in warnings],
                    "review": [asdict(f) for f in reviews],
                },
                indent=2,
            )
        )
    else:
        for finding in failures:
            print(f"FAIL  {finding.path}:{finding.line}  {finding.message}")
        for finding in warnings:
            print(f"WARN  {finding.path}:{finding.line}  {finding.message}")
        for finding in reviews:
            print(f"REVIEW  {finding.path}:{finding.line}  {finding.message}")
        print(f"\n{len(failures)} failure(s), {len(warnings)} warning(s), {len(reviews)} occurrence(s) to review.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
