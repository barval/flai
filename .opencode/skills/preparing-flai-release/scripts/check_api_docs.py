#!/usr/bin/env python3
"""Check that the public API documentation matches the code and itself.

Read-only. Run from the repository root.

``tests/test_api_docs.py`` compares the Flask rules with the spec's paths, and
``tests/test_api_inventory.py`` with its method sets. Neither looks at the
prose: a documented endpoint can be missing from ``docs/API.md``, a spec entry
can have no response schema, and the served Swagger page can disagree with the
file on disk. This script covers those three:

* every operation carries ``responses`` (and ``requestBody`` for a body-bearing
  method), and ``info.version`` equals ``APP_VERSION``;
* every spec path is described in ``docs/API.md`` **and** ``docs/API-ru.md``,
  and every templated path those docs mention exists in the spec;
* the legacy internal spec still says it is stale;
* with ``--live URL``, the served ``/v1/openapi.json`` matches the file and
  ``/v1/docs`` answers ``200``.

Exit codes:
    0  documentation is consistent
    1  at least one finding
    2  usage error or an unreadable file
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a project dependency
    print("error: PyYAML is required (install the project dependencies)", file=sys.stderr)
    raise SystemExit(2) from None

SPEC = "docs/openapi-v1.yaml"
DOCS = ("docs/API.md", "docs/API-ru.md")
LEGACY_SPEC = "docs/openapi.yaml"
LEGACY_MARKERS = ("not maintained", "not served", "not checked against the code")
BODY_METHODS = ("post", "put", "patch")
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
PATH_RE = re.compile(r"/v1/[A-Za-z0-9_\-/{}.]+")


def app_version(root: Path) -> str:
    match = re.search(r'^APP_VERSION = "([^"]+)"', (root / "app" / "config.py").read_text(encoding="utf-8"), re.M)
    if not match:
        print("error: APP_VERSION not found in app/config.py", file=sys.stderr)
        raise SystemExit(2)
    return match.group(1)


def resolve_refs(spec: dict[str, Any], items: list[Any], where: str) -> list[dict[str, Any]]:
    """Expand ``#/components/...`` references so a parameter can be inspected."""
    resolved: list[dict[str, Any]] = []
    for item in items:
        reference = item.get("$ref") if isinstance(item, dict) else None
        if not reference:
            resolved.append(item)
            continue
        target: Any = spec
        for part in reference.lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        if not isinstance(target, dict):
            print(f"warning: {where}: unresolvable reference {reference}", file=sys.stderr)
            continue
        resolved.append(target)
    return resolved


def check_operations(spec: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for path, operations in spec.get("paths", {}).items():
        for method, operation in operations.items():
            if method not in HTTP_METHODS:
                continue
            label = f"{method.upper()} {path}"
            if "responses" not in operation:
                problems.append(f"{label}: no responses")
            if method in BODY_METHODS and "requestBody" not in operation and not operation.get("x-no-body"):
                problems.append(f"{label}: no requestBody (mark x-no-body: true when the method takes none)")
            if not operation.get("summary") and not operation.get("description"):
                problems.append(f"{label}: no summary or description")
            if not operation.get("operationId"):
                problems.append(f"{label}: no operationId")
            declared: set[str] = set()
            for param in resolve_refs(spec, operation.get("parameters", []), Path(path).name):
                if param.get("in") == "path" and param.get("name"):
                    declared.add(param["name"])
            for name in re.findall(r"{([^}]+)}", path):
                if name not in declared:
                    problems.append(f"{label}: path parameter {name} is not declared")
    return problems


def mentions(text: str) -> set[str]:
    """Every templated `/v1` path the document spells out."""
    return {match for match in PATH_RE.findall(text) if "{" in match}


def check_prose(root: Path, spec_paths: set[str]) -> list[str]:
    problems: list[str] = []
    for name in DOCS:
        path = root / name
        if not path.exists():
            problems.append(f"{name}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        for documented in sorted(spec_paths - {m for m in spec_paths if m in text}):
            problems.append(f"{name}: spec path not described: {documented}")
        for extra in sorted(mentions(text) - spec_paths):
            problems.append(f"{name}: documented path absent from the spec: {extra}")
    return problems


def check_legacy(root: Path) -> list[str]:
    path = root / LEGACY_SPEC
    if not path.exists():
        return []
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:12]).lower()
    missing = [marker for marker in LEGACY_MARKERS if marker not in head]
    return [f"{LEGACY_SPEC}: staleness disclaimer is missing {missing}"] if missing else []


def check_live(base: str, spec_paths: set[str]) -> list[str]:
    problems: list[str] = []
    base = base.rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/v1/openapi.json", timeout=10) as response:
            served = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError) as error:
        return [f"live {base}/v1/openapi.json: {error}"]
    served_paths = set(served.get("paths", {}))
    if served_paths != spec_paths:
        problems.append(f"live spec differs from the file: +{sorted(served_paths - spec_paths)} "
                        f"-{sorted(spec_paths - served_paths)}")
    try:
        with urllib.request.urlopen(f"{base}/v1/docs", timeout=10) as response:
            if response.status != 200:
                problems.append(f"live {base}/v1/docs: HTTP {response.status}")
    except urllib.error.URLError as error:
        problems.append(f"live {base}/v1/docs: {error}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_api_docs.py", description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--live", metavar="URL", help="also compare the running instance, e.g. http://localhost:5000")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    spec_file = root / SPEC
    if not spec_file.exists():
        print(f"error: {spec_file} is missing", file=sys.stderr)
        return 2
    try:
        spec = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        print(f"error: {SPEC} does not parse: {error}", file=sys.stderr)
        return 2

    spec_paths = set(spec.get("paths", {}))
    operations = sum(1 for ops in spec["paths"].values() for method in ops if method in HTTP_METHODS)
    expected = f"{app_version(root)}.0"
    declared = str(spec.get("info", {}).get("version", ""))

    problems: list[str] = []
    if declared != expected:
        problems.append(f"{SPEC}: info.version is {declared}, APP_VERSION implies {expected}")
    problems += check_operations(spec)
    problems += check_prose(root, spec_paths)
    problems += check_legacy(root)
    if args.live:
        problems += check_live(args.live, spec_paths)

    report = {
        "paths": len(spec_paths),
        "operations": operations,
        "spec_version": declared,
        "expected_version": expected,
        "live": args.live or None,
        "problems": problems,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{SPEC}: {len(spec_paths)} paths, {operations} operations, version {declared}")
        if args.live:
            print(f"live: {args.live}")
        if problems:
            print(f"\n{len(problems)} finding(s):")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print("The spec, the prose and the code agree.")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
