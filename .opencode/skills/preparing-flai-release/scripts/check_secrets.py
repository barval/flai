#!/usr/bin/env python3
"""Scan tracked files for credential-shaped data before a release.

A release publishes whatever is committed: demo pages, snapshots, fixtures and
docs are downloadable by anyone. This gate looks for the shapes real secrets
take — password hashes, API keys, tokens, private keys — instead of relying on
someone remembering to check.

Usage:
    python3 scripts/check_secrets.py [--root .] [--json]

Exit codes:
    0  clean
    1  findings
    2  usage or git error
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "password hash (scrypt)": re.compile(r"scrypt:\d+:\d+:\d+\$[A-Za-z0-9$+/=]{16,}"),
    "password hash (argon2)": re.compile(r"\$argon2[a-z]{2,}\$"),
    "password hash (pbkdf2)": re.compile(r"pbkdf2:sha256:\d+\$[0-9a-f]{16,}"),
    "Tavily key": re.compile(r"tvly-[A-Za-z0-9]{8,}"),
    "FLAI API key": re.compile(r"flai_[A-Za-z0-9]{16,}"),
    "GitHub token": re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "OpenAI key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "AWS key id": re.compile(r"AKIA[0-9A-Z]{16}"),
    "AWS secret": re.compile(r"aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{40}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "GitLab token": re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

BASE64_PAYLOAD_RE = re.compile(r"data:[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+")

# Documentation and fixtures carry deliberate placeholders; they are not leaks.
PLACEHOLDER_RE = re.compile(
    r"(x{6,}|0{6,}|abcdefgh|12345678|9876543210|example|placeholder|fake|dummy|changeme|your[_-]?key)",
    re.IGNORECASE,
)

# Large generated demo pages are text but slow to scan line by line; they are
# the most likely place for a leak, so they are included on purpose.
MAX_BYTES = 32 * 1024 * 1024
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".ico",
    ".mp4",
    ".webm",
    ".wav",
    ".mp3",
    ".pdf",
    ".gguf",
    ".safetensors",
    ".onnx",
    ".mo",
    ".sqlite",
    ".db",
}


def tracked_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:  # pragma: no cover - env guard
        print(f"git ls-files failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    return [root / name for name in out.decode("utf-8", "replace").split("\0") if name]


def scan(root: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for path in tracked_files(root):
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            # Base64 media is noise: a random 39-character run inside an
            # embedded image matches the Google-key shape by chance.
            line = BASE64_PAYLOAD_RE.sub("", line)
            for label, pattern in PATTERNS.items():
                match = pattern.search(line)
                if not match or PLACEHOLDER_RE.search(match.group(0)):
                    continue
                findings.append(
                    {
                        "file": str(path.relative_to(root)),
                        "line": str(number),
                        "kind": label,
                        "preview": match.group(0)[:24] + "…",
                    }
                )
                break
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    parser.add_argument("--json", action="store_true", help="print machine-readable findings")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not (root / ".git").exists():
        print(f"not a git repository: {root}", file=sys.stderr)
        return 2

    findings = scan(root)
    if args.json:
        print(json.dumps(findings, indent=2))
    elif findings:
        for item in findings:
            print(f"{item['file']}:{item['line']}: {item['kind']}: {item['preview']}")
        print(f"\n{len(findings)} finding(s): remove the data, rotate the credential, and rebuild the artefact.")
    else:
        print("No credential-shaped data in tracked files.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
