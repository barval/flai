# tests/test_monitoring_ranges.py
"""Unit tests for the monitoring bucket math and timezone helper."""

from datetime import UTC, datetime

import pytest
import pytz
from flask import Flask

from app.monitoring.ranges import DEFAULT_RANGE, RANGE_SPECS, bucket_starts
from app.monitoring.timez import app_timezone


def test_range_specs_pin_the_documented_buckets():
    assert {key: int(bucket.total_seconds()) for key, (_window, bucket) in RANGE_SPECS.items()} == {
        "1h": 300,
        "6h": 900,
        "24h": 3600,
        "7d": 21600,
        "30d": 86400,
    }
    assert DEFAULT_RANGE == "24h"


def test_bucket_starts_are_epoch_aligned_and_cover_the_window():
    end = datetime(2026, 10, 23, 12, 34, 56, tzinfo=UTC)

    starts = bucket_starts(end, "24h")

    assert len(starts) == 24
    assert starts[-1] == datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    assert starts[0] == datetime(2026, 10, 22, 13, 0, tzinfo=UTC)
    assert all(start.minute == 0 and start.second == 0 for start in starts)


def test_five_minute_buckets_are_aligned_to_the_clock():
    end = datetime(2026, 10, 23, 12, 34, 56, tzinfo=UTC)

    starts = bucket_starts(end, "1h")

    assert len(starts) == 12
    assert starts[-1] == datetime(2026, 10, 23, 12, 30, tzinfo=UTC)


def test_unknown_range_is_rejected():
    with pytest.raises(KeyError):
        bucket_starts(datetime.now(UTC), "2h")


def test_app_timezone_reads_the_pytz_object_and_defaults_to_utc():
    flask_app = Flask(__name__)
    with flask_app.app_context():
        flask_app.config["TIMEZONE"] = pytz.timezone("Europe/Moscow")
        assert app_timezone() == "Europe/Moscow"

        flask_app.config["TIMEZONE"] = None
        assert app_timezone() == "UTC"
