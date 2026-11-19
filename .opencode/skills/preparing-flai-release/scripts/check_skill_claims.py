#!/usr/bin/env python3
"""Check the agent skills against the repository, the same way docs are checked.

``check_dead_paths.py`` and ``check_doc_claims.py`` read ``AGENTS.md`` and
``docs/`` and nothing else. The skills under ``.opencode/skills/`` are code that
ships with the project: they tell an agent which command to run, which compose
profile to start and which environment key to set. A renamed flag or a dropped
profile rots there silently, and the agent then runs a command that does not
exist.

This script reports four kinds of rot, in every ``SKILL.md``:

* a path in backticks that no longer exists;
* a ``flask <command>`` that ``app/cli.py`` no longer registers;
* a ``with-…`` compose profile that no compose file defines;
* an environment key that neither ``.env.example`` nor the settings registry knows.

It also checks the skill's own inventory: every script in its ``scripts/``
directory must be named in its ``SKILL.md``, so a helper cannot be added and
forgotten by the procedure that is supposed to run it.

Placeholders (``docs/X.md``, ``fix/``, ``with-…``) and HTTP routes are ignored.

Exit codes:
    0  every claim resolves and every script is named
    1  at least one finding
    2  usage error
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TOKEN = re.compile(r"`([^`\n]{2,200})`")
PATH_EXTENSION = re.compile(r"\.[a-z0-9]{1,10}$")
NON_PATH_CHARS = set("*{}<>|$%")
FLASK_CMD = re.compile(r"`?flask\s+([a-z][a-z0-9-]+)")
PROFILE = re.compile(r"\b(with-[a-z0-9-]+)\b")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

# A path claim must start from a real root; anything else is prose.
ROOTS = (
    "app/",
    "modules/",
    "tests/",
    "docs/",
    "prompts/",
    "services/",
    "scripts/",
    "demo/",
    "translations/",
    ".opencode/",
    ".githooks/",
)


def _ignored(root: Path, path: str) -> bool:
    """A path the repository deliberately does not track may be absent in a fresh clone.

    The nested camera clone is the measured case: the skill names its configuration as an
    example of a file that must stay ignored, and a checkout without the clone (CI) has no
    such file. Asking git is the only reliable answer — the ignore rules are the source of
    truth, not a list repeated here.
    """
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", path], cwd=root, capture_output=True
        )
    except OSError:
        return False
    return result.returncode == 0


def _is_path_claim(text: str) -> bool:
    if not text or any(ch in NON_PATH_CHARS for ch in text):
        return False
    if text.startswith(("http://", "https://", "/", "~")):
        return False
    if any(ch in text for ch in (":", "@", " ")):
        return False
    if "/X" in text or text.startswith("X") or "..." in text:
        return False
    if text.startswith(("fix/", "features/", "release-v")):
        # A branch-name pattern, not a file.
        return False
    if text.startswith(ROOTS):
        return bool(PATH_EXTENSION.search(text)) or text.endswith("/")
    return "/" in text and bool(PATH_EXTENSION.search(text))


def _cli_commands(root: Path) -> set[str]:
    text = (root / "app" / "cli.py").read_text(encoding="utf-8", errors="ignore")
    commands = set(re.findall(r'@click\.command\(\s*"([a-z0-9-]+)"', text))
    commands |= set(re.findall(r'@\w+\.cli\.command\(\s*"([a-z0-9-]+)"', text))
    # A decorator without a name registers the function's own name.
    for match in re.finditer(r"@click\.command(?:\(\))?\s*\ndef\s+([a-z_]+)\(", text):
        commands.add(match.group(1).replace("_", "-"))
    return commands


def _compose_profiles(root: Path) -> set[str]:
    profiles: set[str] = set()
    for path in root.glob("docker-compose*.yml"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for block in re.findall(r"profiles:\s*\n((?:\s*-\s*\S+\n?)+)", text):
            profiles |= set(re.findall(r"-\s*([A-Za-z0-9_-]+)", block))
        # `profiles: ["with-crawler"]` is the same claim on one line.
        for inline in re.findall(r"profiles:\s*\[([^\]]+)\]", text):
            profiles |= {item.strip().strip("\"'") for item in inline.split(",") if item.strip()}
    return profiles


def _known_env_keys(root: Path) -> set[str]:
    keys = set(re.findall(r"^#?\s*([A-Z0-9_]+)=", (root / ".env.example").read_text(encoding="utf-8"), re.M))
    registry = (root / "app" / "settings_registry.py").read_text(encoding="utf-8", errors="ignore")
    keys |= set(re.findall(r'"([A-Z][A-Z0-9_]{2,})"', registry))
    config = (root / "app" / "config.py").read_text(encoding="utf-8", errors="ignore")
    keys |= set(re.findall(r'getenv\(\s*"([A-Z0-9_]+)"', config))
    keys |= set(re.findall(r'setting\(\s*"([A-Z0-9_]+)"', config))
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    skills_root = root / ".opencode" / "skills"
    if not skills_root.is_dir():
        print(f"no skills directory: {skills_root}", file=sys.stderr)
        return 2

    commands = _cli_commands(root)
    profiles = _compose_profiles(root)
    env_keys = _known_env_keys(root)

    findings: list[tuple[str, str, int, str]] = []  # file, kind, line, detail

    for skill in sorted(skills_root.rglob("SKILL.md")):
        relative = str(skill.relative_to(root))
        text = skill.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()

        for number, line in enumerate(lines, 1):
            for match in TOKEN.finditer(line):
                token = match.group(1).strip().split("#", 1)[0].rstrip("/")
                if (
                    _is_path_claim(token)
                    and not (root / token).exists()
                    and not _ignored(root, token)
                ):
                    findings.append((relative, "path", number, token))

            for match in FLASK_CMD.finditer(line):
                name = match.group(1)
                if name not in commands and not name.startswith(("admin", "db", "shell", "routes")):
                    findings.append((relative, "flask", number, name))

            for match in PROFILE.finditer(line):
                name = match.group(1).rstrip("-")
                if name.endswith("-") or name in {"with-…", "with-..."}:
                    continue
                if name not in profiles:
                    findings.append((relative, "profile", number, name))

            for match in TOKEN.finditer(line):
                token = match.group(1).strip()
                if not ENV_KEY.match(token) or "_" not in token:
                    continue
                # Only a key written the way an environment is used — `KEY=value` or
                # `export KEY` — not a constant (`APP_VERSION`) or a shell variable.
                if not re.search(rf"(?:^|\s)(?:export\s+)?{re.escape(token)}\s*=", line):
                    continue
                if token not in env_keys and not token.startswith(("SCHEDULER_RUN_", "MCP_", "NET")):
                    findings.append((relative, "env", number, token))

        scripts = sorted((skill.parent / "scripts").glob("*.py"))
        for script in scripts:
            if script.name not in text:
                findings.append((relative, "script_inventory", 0, script.name))

    if args.json:
        print(
            json.dumps(
                [{"file": f, "kind": k, "line": n, "detail": d} for f, k, n, d in findings],
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        for file_name, kind, number, detail in findings:
            location = f"{file_name}:{number}" if number else file_name
            print(f"{kind.upper():16} {location}  {detail}")
        print(f"\n{len(findings)} unresolved skill claim(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
