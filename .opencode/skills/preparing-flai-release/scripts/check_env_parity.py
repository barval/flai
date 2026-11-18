#!/usr/bin/env python3
"""Compare the environment keys the code reads with the template it ships.

``app/config.py`` reads a key; ``.env.example`` documents it. The release
workflow diffs ``.env`` against ``.env.example`` — that catches a key added to
the deployment but not to the template, and nothing else: a key added to the
code and forgotten in the template reaches a fresh installation as a silent
default, and nobody sees the gap.

Two directions, with the deliberate exceptions:

* **missing** — read by ``app/config.py``, absent from ``.env.example``, and not
  a panel setting. A panel setting lives in the database, so the template
  correctly omits it (``DEPTH_VERIFY`` is one).
* **unused** — in ``.env.example`` and read by nothing. Reported, but only a
  failure with ``--strict``: a compose file may read the key itself
  (``CRAWL4AI_API_TOKEN``), and this script reads the application.

Exit codes:
    0  no missing key (and, with ``--strict``, no unused one)
    1  at least one finding
    2  usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _read_config_keys(config: Path) -> set[str]:
    text = config.read_text(encoding="utf-8", errors="ignore")
    keys = set(re.findall(r'getenv\(\s*"([A-Z0-9_]+)"', text))
    keys |= set(re.findall(r'setting\(\s*"([A-Z0-9_]+)"', text))
    # BOOTSTRAP_KEYS names the keys that stay environment-only on purpose.
    block = re.search(r"BOOTSTRAP_KEYS\s*=\s*[({](.*?)[)}]", text, re.S)
    if block:
        keys |= set(re.findall(r'"([A-Z0-9_]+)"', block.group(1)))
    return keys


def _template_keys(template: Path) -> set[str]:
    # Optional keys are commented out; a plain `^KEY=` would call them missing.
    return set(re.findall(r"^#?\s*([A-Z0-9_]+)=", template.read_text(encoding="utf-8"), re.M))


def _panel_keys(registry: Path) -> set[str]:
    text = registry.read_text(encoding="utf-8", errors="ignore")
    return set(re.findall(r'"([A-Z][A-Z0-9_]{2,})"', text))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="an unused template key is a failure too")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[4]
    config = root / "app" / "config.py"
    template = root / ".env.example"
    registry = root / "app" / "settings_registry.py"
    if not config.exists() or not template.exists():
        print(f"not a repository root: {root}", file=sys.stderr)
        return 2

    code_keys = _read_config_keys(config)
    template_keys = _template_keys(template)
    panel_keys = _panel_keys(registry) if registry.exists() else set()

    missing = sorted(code_keys - template_keys - panel_keys)
    unused = sorted(template_keys - code_keys - panel_keys)

    if args.json:
        print(json.dumps({"missing": missing, "unused": unused}, ensure_ascii=False, indent=1))
    else:
        for key in missing:
            print(f"MISSING  {key}  read by app/config.py, absent from .env.example")
        for key in unused:
            print(f"UNUSED   {key}  in .env.example, read by neither the code nor a panel setting")
        print(f"\n{len(missing)} missing key(s), {len(unused)} unused key(s)")

    failed = bool(missing) or (args.strict and bool(unused))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
