# tests/test_monitoring_system.py
"""Host-load samples: parsing, bucketing and the payload contract."""

from datetime import UTC, datetime, timedelta

import pytest

from app.monitoring.system import (
    bucket_average,
    cpu_percent,
    parse_cpu_line,
    parse_meminfo,
    parse_nvidia_smi,
)

pytestmark = pytest.mark.unit


def test_cpu_percent_from_two_readings():
    # 100 of 400 jiffies idle → 75 % busy
    previous = {"idle": 900, "total": 1400}
    assert cpu_percent(idle=1000, total=1800, previous=previous) == 75.0


def test_cpu_percent_without_a_previous_reading_is_unknown():
    assert cpu_percent(idle=1000, total=1800, previous={"idle": None, "total": None}) is None


def test_cpu_percent_ignores_a_clock_that_did_not_move():
    assert cpu_percent(idle=10, total=10, previous={"idle": 10, "total": 10}) is None


def test_parse_cpu_line_reads_idle_and_total():
    idle, total = parse_cpu_line("cpu  100 20 30 400 50 0 0 0 0 0")

    assert idle == 450  # idle + iowait
    assert total == 600


def test_parse_meminfo_reports_used_and_total():
    text = "MemTotal:       32000000 kB\nMemFree:  1000000 kB\nMemAvailable:  8000000 kB\n"

    used, total = parse_meminfo(text)

    assert total == 31250
    assert used == total - 7812  # MemAvailable 8 000 000 kB


def test_parse_nvidia_smi_reads_utilisation_and_memory():
    assert parse_nvidia_smi("37, 7694, 16311\n") == (37.0, 7694, 16311)


def test_parse_nvidia_smi_without_a_gpu_is_none():
    assert parse_nvidia_smi("") is None
    assert parse_nvidia_smi("No devices were found\n") is None


def test_buckets_average_only_the_samples_inside_them():
    start = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    starts = [start, start + timedelta(minutes=5)]
    rows = [
        (start + timedelta(minutes=1), 10.0),
        (start + timedelta(minutes=3), 20.0),
        (start + timedelta(minutes=6), 50.0),
        (start + timedelta(minutes=7), None),
    ]

    assert bucket_average(rows, starts, 300) == [15.0, 50.0]


def test_a_bucket_without_samples_stays_empty():
    start = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    starts = [start, start + timedelta(minutes=5)]

    assert bucket_average([], starts, 300) == [None, None]
