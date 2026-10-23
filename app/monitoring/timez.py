"""Timezone resolution for the monitoring SQL.

The configured ``TIMEZONE`` is a pytz object (or ``None``), while PostgreSQL's
``AT TIME ZONE`` needs a zone name, so every query receives the resolved name.
"""

import pytz
from flask import current_app


def app_timezone() -> str:
    """The configured TIMEZONE when pytz accepts it, otherwise UTC."""
    try:
        tz = current_app.config.get("TIMEZONE")
    except RuntimeError:
        return "UTC"

    name = tz if isinstance(tz, str) else getattr(tz, "zone", None)
    if not name:
        return "UTC"

    try:
        pytz.timezone(name)
    except Exception:
        return "UTC"
    return name
