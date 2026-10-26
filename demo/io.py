"""Deterministic JSON storage shared by the demo builder and the capture client."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = Path(__file__).resolve().parent

_VERSION_RE = re.compile(r'^APP_VERSION = "([^"]+)"$', re.MULTILINE)


class SnapshotError(RuntimeError):
    """Raised when a value the demo depends on cannot be read."""


def read_app_version(root: Path = ROOT) -> str:
    """Read APP_VERSION from the source instead of importing the config module."""
    config = (root / "app" / "config.py").read_text(encoding="utf-8")
    match = _VERSION_RE.search(config)
    if not match:
        raise SnapshotError(f"APP_VERSION not found in {config}")
    return match.group(1)


def save_snapshot(path: Path, data: dict[str, Any]) -> None:
    """Write a snapshot so that equal data always produces equal bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def load_snapshot(path: Path) -> dict[str, Any]:
    """Read a snapshot written by :func:`save_snapshot`."""
    return json.loads(path.read_text(encoding="utf-8"))
