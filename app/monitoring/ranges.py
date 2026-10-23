"""Range definitions and UTC bucket math for the admin monitoring tab."""

from datetime import UTC, datetime, timedelta

RANGE_SPECS: dict[str, tuple[timedelta, timedelta]] = {
    "1h": (timedelta(hours=1), timedelta(minutes=5)),
    "6h": (timedelta(hours=6), timedelta(minutes=15)),
    "24h": (timedelta(hours=24), timedelta(hours=1)),
    "7d": (timedelta(days=7), timedelta(hours=6)),
    "30d": (timedelta(days=30), timedelta(days=1)),
}
DEFAULT_RANGE = "24h"


def bucket_starts(end: datetime, range_key: str) -> list[datetime]:
    """UTC bucket starts aligned to the epoch, oldest first, covering [end-window, end)."""
    window, bucket = RANGE_SPECS[range_key]
    size = int(bucket.total_seconds())
    end_utc = end.astimezone(UTC) if end.tzinfo is not None else end.replace(tzinfo=UTC)
    last_start = int(end_utc.timestamp()) // size * size
    count = int(window.total_seconds() // size)
    return [datetime.fromtimestamp(last_start - index * size, tz=UTC) for index in range(count - 1, -1, -1)]
