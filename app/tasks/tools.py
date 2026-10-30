# app/tasks/tools.py
"""Task capability views, derived from the manifest.

The manifest itself lives in :mod:`app.tool_registry`; this module only exposes
the names the rest of the subsystem already imports (``CAPABILITIES``,
``TOOL_BADGES``, ``TOOL_RUNNERS``, ``get_capabilities``), so nothing else had to
change when the literals moved.

``TOOL_RUNNERS`` remains the seam for direct tool runners: the calculation is the
one runner simple enough to be a plain callable today, because the others need
the step context the executor assembles.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.tool_registry import badges as _badges
from app.tool_registry import capabilities as _capabilities


def _run_calculate(expression: str) -> Any:
    from app.tools import safe_eval

    return safe_eval(expression)


TOOL_RUNNERS: dict[str, Callable] = {"calculate": _run_calculate}

# Display badge for a step whose content a TOOL produced (not the model):
# emoji + the gettext msgid of its localized name. The API layer renders the
# msgid inside the request locale and prefixes the step's answer line with it.
TOOL_BADGES: dict[str, tuple[str, str]] = _badges()

CAPABILITIES: list[dict[str, str]] = _capabilities()


def get_capabilities() -> list[dict[str, str]]:
    return [dict(c) for c in CAPABILITIES]
