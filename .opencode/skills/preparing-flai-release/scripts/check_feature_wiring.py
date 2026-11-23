#!/usr/bin/env python3
"""Every user-facing feature must be wired into every place a person meets it.

The release gate checks each surface on its own: docs claims, dead paths, compose
profiles, deploy keys, the capability list. None of those answers the question that
actually went wrong in v13.6 — a feature was implemented and documented but nobody
had wired it into the router, so "compose a song" was answered as a video request,
and the README's full-stack example never named the profile.

This gate is deliberately a table, not a heuristic: a new feature adds one entry
naming every file and the exact string that must be there. Adding the entry is the
step that forces the author to walk the surfaces.

Exit codes:
    0  every feature is wired everywhere it claims
    1  at least one missing marker
    2  a file the table needs is missing
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

# feature -> list of (file, marker)
FEATURES: dict[str, list[tuple[str, str]]] = {
    "music": [
        # the tool exists and the model is told to call it
        ("app/tools.py", '"generate_music"'),
        ("prompts/en/chat.template", "generate_music"),
        ("prompts/ru/chat.template", "generate_music"),
        # routing knows it is not video/image
        ("prompts/en/base_text.template", "generate_music"),
        ("prompts/ru/base_text.template", "generate_music"),
        ("prompts/en/kernel.template", "generate_music"),
        # queue and configuration
        ("app/queue.py", '"music_gen"'),
        ("app/config.py", "MUSIC_URL"),
        ("docs/CONFIGURATION.md", "MUSIC_URL"),
        ("docs/CONFIGURATION-ru.md", "MUSIC_URL"),
        # deploy: script, profiles, agent skill
        ("deploy.sh", "--with-music"),
        ("deploy-ru.sh", "--with-music"),
        (".opencode/skills/deploying-flai/SKILL.md", "--with-music"),
        ("docker-compose.gpu.base.yml", "with-music"),
        ("docker-compose.cpu.yml", "with-music"),
        # README: what's new, requirements, quick start, full stack
        ("README.md", "with-music"),
        ("README.md", "--with-music"),
        ("README.md", "--profile with-music"),
        ("README-ru.md", "with-music"),
        ("README-ru.md", "--with-music"),
        ("README-ru.md", "--profile with-music"),
        ("README.md", "**Music and songs**"),
        ("README-ru.md", "**Музыка и песни**"),
        # its own documentation, both languages
        ("docs/MUSIC.md", "with-music"),
        ("docs/MUSIC-ru.md", "with-music"),
        # architecture and FAQ enumerate the profiles; the capability list names the tool
        ("docs/ARCHITECTURE.md", "with-music"),
        ("docs/ARCHITECTURE-ru.md", "with-music"),
        ("docs/FAQ.md", "with-music"),
        ("docs/FAQ-ru.md", "with-music"),
        ("prompts/en/skills.txt", "compose music"),
        ("prompts/ru/skills.txt", "сочиняю музыку"),
    ],
}


def main() -> int:
    findings: list[str] = []
    for feature, checks in FEATURES.items():
        for rel, marker in checks:
            path = ROOT / rel
            if not path.exists():
                findings.append(f"MISSING FILE  {feature}: {rel}")
                continue
            if marker.lower() not in path.read_text(encoding="utf-8").lower():
                findings.append(f"NOT WIRED     {feature}: {rel} has no {marker!r}")

    for finding in findings:
        print(finding)
    print(f"\n{len(findings)} finding(s); {len(FEATURES)} feature(s) wired")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
