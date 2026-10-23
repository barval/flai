"""Hourly storage snapshots and their carry-forward series."""

import logging
import threading
from datetime import UTC, datetime

from flask import Flask

from app.database import get_db
from app.monitoring.archive import prune_archive, prune_web_requests
from app.settings_store import prune_audit

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_S = 600


def _current_hour(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(UTC)
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def write_snapshot(now: datetime | None = None) -> int:
    """Store this hour's usage per user; returns the number of inserted rows."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO storage_snapshots (user_id, taken_at, used_bytes)
            SELECT user_id, %(taken_at)s, used_bytes FROM user_storage
            ON CONFLICT DO NOTHING
            """,
            {"taken_at": _current_hour(now)},
        )
        return cursor.rowcount


def storage_series(
    logins: list[str],
    starts: list[datetime],
    until: datetime,
) -> tuple[list[int | None], datetime | None]:
    """Carry the last known usage forward; ``None`` buckets have no snapshot yet.

    Only one look-back row per user plus the rows inside the range are read, so
    the cost stays bounded by the selected period instead of the whole history.
    """
    if not logins or not starts:
        return [None] * len(starts), None

    users = list(logins)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT ON (user_id) user_id, taken_at, used_bytes
            FROM storage_snapshots
            WHERE user_id = ANY(%s) AND taken_at < %s
            ORDER BY user_id, taken_at DESC
            """,
            (users, starts[0]),
        )
        rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT user_id, taken_at, used_bytes
            FROM storage_snapshots
            WHERE user_id = ANY(%s) AND taken_at >= %s AND taken_at <= %s
            ORDER BY user_id, taken_at
            """,
            (users, starts[0], until),
        )
        rows += cursor.fetchall()
        cursor.execute(
            "SELECT MIN(taken_at) AS first_seen FROM storage_snapshots "
            "WHERE user_id = ANY(%s) AND taken_at <= %s",
            (users, until),
        )
        first_seen = cursor.fetchone()["first_seen"]

    per_user: dict[str, list[tuple[datetime, int]]] = {login: [] for login in logins}
    for row in rows:
        per_user.setdefault(row["user_id"], []).append((row["taken_at"], row["used_bytes"]))
    for values in per_user.values():
        values.sort()

    series: list[int | None] = [None] * len(starts)
    span = starts[1] - starts[0] if len(starts) > 1 else (until - starts[0])
    for values in per_user.values():
        pointer = 0
        current: int | None = None
        for index, start in enumerate(starts):
            # A bucket shows the value as of its END: a daily bucket opened at
            # midnight must pick up the snapshots taken during that day, not
            # only the ones from before its start.
            bucket_end = start + span
            while pointer < len(values) and values[pointer][0] < bucket_end:
                current = values[pointer][1]
                pointer += 1
            if current is not None:
                series[index] = (series[index] or 0) + current
    return series, first_seen


def start_snapshotter(app: Flask) -> None:
    """Start the daemon thread that stores storage usage once per hour."""
    running = getattr(app, "_monitoring_thread", None)
    if running is not None and running.is_alive():
        return

    stop_event = threading.Event()
    app._monitoring_stop_event = stop_event

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                with app.app_context():
                    write_snapshot()
                    prune_archive()
                    prune_web_requests()
                    prune_audit()
            except Exception as exc:
                logger.warning(f"Monitoring snapshot failed: {exc}")
            if stop_event.wait(SNAPSHOT_INTERVAL_S):
                return

    thread = threading.Thread(target=_loop, name="flai-monitoring-snapshots", daemon=True)
    app._monitoring_thread = thread
    thread.start()


def stop_snapshotter(app: Flask, timeout: float = 5) -> bool:
    """Stop the snapshot thread; returns whether it exited."""
    stop_event = getattr(app, "_monitoring_stop_event", None)
    thread = getattr(app, "_monitoring_thread", None)
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
        return not thread.is_alive()
    return True
