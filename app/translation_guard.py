"""A broken compiled catalog must degrade the language, not break the page.

Measured on the live deployment: `translations/en/LC_MESSAGES/messages.mo` was
left at zero bytes by an interrupted `pybabel compile` (it writes in place), and
every template rendered in English answered 500 with
`struct.error: unpack requires a buffer of 4 bytes` — the whole admin panel was
down for the operator, while the Russian interface kept working and hid the
cause.

The guard runs at startup: a catalog that is empty or does not start with the GNU
`.mo` magic is moved aside (nothing is lost, the file is renamed) and the
interface falls back to the message ids, which are English text. The next
`pybabel compile` restores the file.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Every GNU gettext catalog starts with this little-endian magic.
MO_MAGIC = b"\xde\x12\x04\x95"
DOMAINS = ("messages",)


def check_catalogs(translations_dir: str | Path, languages: tuple[str, ...] = ("en", "ru")) -> list[str]:
    """Move aside unusable ``.mo`` files; returns the paths that were moved.

    Silent when everything is fine: this runs on every start.
    """
    root = Path(translations_dir)
    broken: list[str] = []
    for language in languages:
        for domain in DOMAINS:
            catalog = root / language / "LC_MESSAGES" / f"{domain}.mo"
            if not catalog.is_file():
                continue
            try:
                head = catalog.open("rb").read(len(MO_MAGIC))
            except OSError as exc:
                logger.warning(f"Translation catalog {catalog} is unreadable: {exc}")
                continue
            if head == MO_MAGIC:
                continue
            backup = catalog.with_suffix(".mo.broken")
            try:
                catalog.replace(backup)
            except OSError as exc:
                logger.error(f"Translation catalog {catalog} is broken and could not be moved aside: {exc}")
                continue
            logger.error(
                f"Translation catalog {catalog} is not a compiled .mo ({len(head)} bytes) — "
                f"moved to {backup.name}; the {language} interface falls back to English. "
                f"Fix with: pybabel compile -d translations"
            )
            broken.append(str(catalog))
    return broken
