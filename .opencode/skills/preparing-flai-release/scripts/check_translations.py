#!/usr/bin/env python3
"""Check FLAI translation catalogs for parity, completeness and compilation.

Read-only. Run from the repository root.

The script compares msgids between the English and Russian ``.po`` catalogs,
reports empty and fuzzy entries, and verifies that every compiled ``.mo``
catalog matches its source.

Exit codes:
    0  catalogs are complete and compiled
    1  at least one finding
    2  a required dependency or catalog is missing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from babel.messages.mofile import read_mo
    from babel.messages.pofile import read_po
except ImportError:  # pragma: no cover - Babel is a Flask-Babel dependency
    print("error: Babel is required (install the project dependencies)", file=sys.stderr)
    raise SystemExit(2) from None


def message_key(message: Any) -> tuple[str, Any]:
    return (message.context or "", message.id)


def display_key(key: tuple[str, Any]) -> str:
    context, message_id = key
    if isinstance(message_id, tuple):
        message_id = " | ".join(str(part) for part in message_id)
    return f"{context}: {message_id}" if context else str(message_id)


def is_empty(value: Any) -> bool:
    if isinstance(value, (tuple, list)):
        return not any(str(part).strip() for part in value)
    return not str(value or "").strip()


def load_catalog(path: Path) -> dict[tuple[str, Any], Any]:
    with path.open("rb") as handle:
        catalog = read_po(handle)
    messages: dict[tuple[str, Any], Any] = {}
    for message in catalog:
        if not message.id or getattr(message, "obsolete", False):
            continue
        messages[message_key(message)] = message
    return messages


def compiled_ids(path: Path) -> dict[tuple[str, Any], Any] | None:
    if not path.exists():
        return None
    with path.open("rb") as handle:
        catalog = read_mo(handle)
    messages: dict[tuple[str, Any], Any] = {}
    for message in catalog:
        if not message.id:
            continue
        messages[message_key(message)] = message
    return messages


def check_catalog(language: str, root: Path, max_issues: int) -> tuple[list[str], dict[str, Any]]:
    issues: list[str] = []
    po_path = root / "translations" / language / "LC_MESSAGES" / "messages.po"
    mo_path = po_path.with_suffix(".mo")
    summary: dict[str, Any] = {"language": language, "po": str(po_path), "mo": str(mo_path)}
    if not po_path.exists():
        return [f"{language}: missing {po_path}"], summary

    messages = load_catalog(po_path)
    summary["po_messages"] = len(messages)

    empty = [display_key(key) for key, message in messages.items() if is_empty(message.string)]
    fuzzy = [display_key(key) for key, message in messages.items() if getattr(message, "fuzzy", False)]
    summary["empty"] = len(empty)
    summary["fuzzy"] = len(fuzzy)
    if empty:
        issues.append(f"{language}: {len(empty)} empty translations")
        issues.extend(f"  empty: {key}" for key in empty[:max_issues])
    if fuzzy:
        issues.append(f"{language}: {len(fuzzy)} fuzzy translations")
        issues.extend(f"  fuzzy: {key}" for key in fuzzy[:max_issues])

    compiled = compiled_ids(mo_path)
    if compiled is None:
        issues.append(f"{language}: missing compiled catalog {mo_path}")
    else:
        expected = {
            key: message
            for key, message in messages.items()
            if not getattr(message, "fuzzy", False) and not is_empty(message.string)
        }
        missing = sorted(expected.keys() - compiled.keys(), key=display_key)
        extra = sorted(compiled.keys() - messages.keys(), key=display_key)
        summary["mo_messages"] = len(compiled)
        if missing:
            issues.append(f"{language}: {len(missing)} source messages missing from the compiled catalog")
            issues.extend(f"  missing in .mo: {display_key(key)}" for key in missing[:max_issues])
        if extra:
            issues.append(f"{language}: {len(extra)} compiled messages absent from the source catalog")
            issues.extend(f"  extra in .mo: {display_key(key)}" for key in extra[:max_issues])

    return issues, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root (default: .)")
    parser.add_argument("--max-issues", type=int, default=50, help="maximum issues printed per category")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    max_issues = max(1, args.max_issues)
    all_issues: list[str] = []
    summaries: list[dict[str, Any]] = []
    for language in ("en", "ru"):
        issues, summary = check_catalog(language, args.root, max_issues)
        all_issues.extend(issues)
        summaries.append(summary)

    parity: dict[str, Any] = {}
    en_po = args.root / "translations/en/LC_MESSAGES/messages.po"
    ru_po = args.root / "translations/ru/LC_MESSAGES/messages.po"
    if en_po.exists() and ru_po.exists():
        en = load_catalog(en_po)
        ru = load_catalog(ru_po)
        only_en = sorted(en.keys() - ru.keys(), key=display_key)
        only_ru = sorted(ru.keys() - en.keys(), key=display_key)
        parity = {"only_en": len(only_en), "only_ru": len(only_ru)}
        if only_en:
            all_issues.append(f"catalogs: {len(only_en)} message ids only in en")
            all_issues.extend(f"  only en: {display_key(key)}" for key in only_en[:max_issues])
        if only_ru:
            all_issues.append(f"catalogs: {len(only_ru)} message ids only in ru")
            all_issues.extend(f"  only ru: {display_key(key)}" for key in only_ru[:max_issues])

    if args.json:
        print(json.dumps({"issues": all_issues, "languages": summaries, "parity": parity}, indent=2))
    else:
        for summary in summaries:
            print(
                f"{summary['language']}: .po={summary.get('po_messages', 'missing')} "
                f".mo={summary.get('mo_messages', 'missing')} "
                f"empty={summary.get('empty', '?')} fuzzy={summary.get('fuzzy', '?')}"
            )
        if parity:
            print(f"parity: only_en={parity['only_en']} only_ru={parity['only_ru']}")
        if all_issues:
            print()
            for issue in all_issues:
                print(issue)
        else:
            print("Translations are complete and compiled.")

    return 0 if not all_issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
