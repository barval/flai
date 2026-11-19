#!/usr/bin/env python3
"""The mypy ratchet: the check runs, and every silence in it is temporary.

`mypy app/ modules/` now fails CI on any new error. The modules that still carry the
backlog found when the check was switched on are listed in `pyproject.toml` with
`ignore_errors = true`; this script keeps that list honest:

* an entry whose module no longer reports anything is a module that was fixed — the line
  has to go, or the ratchet stops ratcheting;
* an entry that names no file is a typo that silently suppresses nothing;
* the list may only shrink. A new error anywhere else already fails the full run.

It checks the listed modules with a copy of the configuration that omits the overrides, so
the errors are visible again. That is the only way to tell "fixed" from "silenced".

Exit codes:
    0  the list matches reality
    1  stale entries, or an entry with no file
    2  mypy is not installed, or could not run
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def _mypy_settings(config: dict) -> dict:
    return config.get("tool", {}).get("mypy", {})


def _silenced_modules(settings: dict) -> list[str]:
    modules: list[str] = []
    for override in settings.get("overrides", []) or []:
        if override.get("ignore_errors") is True:
            listed = override.get("module")
            if isinstance(listed, str):
                listed = [listed]
            modules.extend(listed or [])
    return sorted(set(modules))


def _config_without_overrides(settings: dict) -> str:
    """A minimal ini with the same global switches and no per-module silence."""
    lines = ["[mypy]"]
    for key in ("python_version", "warn_return_any", "ignore_missing_imports", "exclude"):
        value = settings.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            value = "[" + ", ".join(f'"{item}"' for item in value) + "]"
        lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    settings = _mypy_settings(tomllib.loads(pyproject.read_text(encoding="utf-8")))
    modules = _silenced_modules(settings)

    missing = [module for module in modules if not (root / (module.replace(".", "/") + ".py")).exists()]
    if missing:
        for module in missing:
            print(f"NO FILE     {module}")
        print(f"\n{len(missing)} entry(ies) name no file; a typo silences nothing")
        return 1

    if not modules:
        print("0 silenced module(s); the check covers the whole project")
        return 0

    files = [module.replace(".", "/") + ".py" for module in modules]
    with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False, encoding="utf-8") as handle:
        handle.write(_config_without_overrides(settings))
        config_path = handle.name

    command = [sys.executable, "-m", "mypy", *files, "--config-file", config_path, "--no-error-summary"]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=900)
    Path(config_path).unlink(missing_ok=True)

    if "No module named mypy" in result.stderr or "No module named mypy" in result.stdout:
        print("mypy is not installed; install the dev extras first", file=sys.stderr)
        return 2
    if result.returncode not in (0, 1):
        print(result.stdout.rstrip(), file=sys.stderr)
        print(result.stderr.rstrip(), file=sys.stderr)
        return 2

    failing = {
        line.split(":", 1)[0]
        for line in result.stdout.splitlines()
        if ": error:" in line
    }
    stale = [module for module in modules if module.replace(".", "/") + ".py" not in failing]

    for module in stale:
        print(f"FIXED       {module}")
    if stale:
        print(
            f"\n{len(stale)} module(s) in the ratchet list report nothing; remove them from"
            " [tool.mypy.overrides] in pyproject.toml"
        )
        return 1

    print(f"{len(modules)} silenced module(s) still report at least one error; the ratchet holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
