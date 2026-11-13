#!/usr/bin/env python3
"""Check that documentation names things the project actually has.

Prose is audited by a person; what a script can settle, a script should settle.
This reads ``AGENTS.md``, the READMEs and ``docs/*.md`` and reports three kinds of
claim that go stale silently:

* a ``flask <command>`` that no longer exists;
* an environment key that is in neither ``.env.example`` nor the settings registry
  (so the docs advertise a knob nothing reads);
* a compose ``with-…`` profile that no compose file defines.

Read-only. Exit 0 when every claim resolves, 1 on findings, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
DOCS = ["AGENTS.md", "README.md", "README-ru.md"]
DOCS_DIR = REPO / "docs"

#: Click commands that come from Flask itself, not from this project.
CLICK_BUILTINS = {
    "run",
    "shell",
    "routes",
    "db",
    "deploy",
    "init",
    "makemigrations",
    "migrate",
    "upgrade",
    "downgrade",
    "seed",
    "show",
    "edit",
    "merge",
    "revision",
    "current",
    "heads",
    "history",
    "stamp",
}

#: Variables owned by a service that documents itself. The camera service is a
#: nested clone excluded from this tree, so its keys cannot be resolved here.
EXTERNAL_KEYS = {"CAMERAS_CONF", "CAMERAS_AUTH", "ROOMS_I18N"}

FLASK_COMMAND = re.compile(r"\bflask\s+([a-z][a-z0-9-]{2,})")
PROFILE = re.compile(r"\bwith-[a-z][a-z0-9-]{2,}")
ENV_KEY = re.compile(r"`([A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+)`")


def _project_commands() -> set[str]:
    text = (REPO / "app" / "cli.py").read_text(encoding="utf-8", errors="ignore")
    return set(re.findall(r'@click\.command\("([a-z0-9-]+)"\)', text))


def _exists_outside_documentation(key: str) -> bool:
    """True when the key is read, written or passed anywhere but the prose.

    A settings key lives in `.env.example`, in the registry, in `app/config.py` as
    a `os.getenv` fallback, in a compose `environment:` block or in the code that
    consumes it — the point is that *something* other than a document names it.
    """
    import subprocess

    result = subprocess.run(
        [
            "git",
            "grep",
            "-q",
            "-F",
            "-e",
            key,
            "--",
            ".",
            ":!docs",
            ":!AGENTS.md",
            ":!README.md",
            ":!README-ru.md",
        ],
        cwd=REPO,
        capture_output=True,
    )
    return result.returncode == 0


def _known_profiles() -> set[str]:
    profiles: set[str] = set()
    for path in REPO.glob("docker-compose*.yml"):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith("- with-"):
                profiles.add(stripped[2:].strip())
            elif stripped.startswith("profiles:"):
                profiles.update(re.findall(r"with-[a-z0-9-]+", stripped))
    return profiles


def _documents() -> list[Path]:
    paths = [REPO / name for name in DOCS]
    paths.extend(sorted(DOCS_DIR.glob("*.md")))
    return [path for path in paths if path.exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if not (REPO / "AGENTS.md").exists():
        print(f"not a repository root: {REPO}", file=sys.stderr)
        return 2

    commands = _project_commands()
    profiles = _known_profiles()
    env_cache: dict[str, bool] = {}

    findings: list[dict[str, str]] = []
    for document in _documents():
        relative = str(document.relative_to(REPO))
        for number, line in enumerate(document.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.lstrip().startswith((">>>", "#    ")):
                continue
            for name in FLASK_COMMAND.findall(line):
                if name in CLICK_BUILTINS or name in commands:
                    continue
                findings.append({"kind": "command", "file": relative, "line": str(number), "value": name})
            for match in PROFILE.finditer(line):
                profile = match.group(0)
                # `#-working-with-document-search` is a TOC anchor and `with-tts-`
                # is a truncated family name; neither is a profile claim.
                # A TOC entry carries the profile name inside its anchor:
                # `[Working with document search](#-working-with-document-search)`.
                in_link = line.rfind("](", 0, match.start()) != -1 and line.find(")", match.end()) != -1
                if profile.endswith("-") or in_link:
                    continue
                if profile not in profiles:
                    findings.append({"kind": "profile", "file": relative, "line": str(number), "value": profile})
            for key in ENV_KEY.findall(line):
                if key in EXTERNAL_KEYS:
                    continue
                if key not in env_cache:
                    env_cache[key] = _exists_outside_documentation(key)
                if env_cache[key]:
                    continue
                findings.append({"kind": "env", "file": relative, "line": str(number), "value": key})

    if args.json:
        import json

        print(json.dumps(findings, ensure_ascii=False, indent=1))
    else:
        for item in findings:
            print(f"{item['kind'].upper():8} {item['file']}:{item['line']}  {item['value']}")
        print(f"\n{len(findings)} unresolved claim(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
