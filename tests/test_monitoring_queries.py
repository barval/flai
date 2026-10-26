# tests/test_monitoring_queries.py
"""Aggregation tests: live rows plus the deletion archive."""

import pytest
import pytz

from app.db import delete_session_and_messages
from app.monitoring.queries import SERIES_KEYS, build_payload
from tests._monitoring_fixtures import NOW, seed_monitoring_data

pytestmark = pytest.mark.requires_db


def _payload(test_app, range_key="24h", logins=("u1",)):
    test_app.config["TIMEZONE"] = pytz.UTC
    return build_payload(list(logins), range_key, now=NOW)


def test_payload_shape_and_alignment(test_app):
    with test_app.app_context():
        seed_monitoring_data()

        payload = _payload(test_app)

    assert payload["range"] == "24h"
    assert payload["bucket_seconds"] == 3600
    assert len(payload["buckets"]) == 24
    for key in SERIES_KEYS:
        assert len(payload["series"][key]) == 24, f"{key} must align with the buckets"
    assert payload["totals"]["sessions"] == sum(v for v in payload["series"]["sessions"] if v)
    assert payload["storage"]["limit_bytes"] == 500 * 1024 * 1024


def test_tokens_sum_chat_tasks_and_plans(test_app):
    with test_app.app_context():
        seed_monitoring_data()

        payload = _payload(test_app)

    assert payload["totals"]["outgoing_tokens"] == 1500 + 700 + 300
    assert payload["totals"]["incoming_tokens"] == 500 + 200 + 100
    assert isinstance(payload["totals"]["outgoing_tokens"], int)


def test_documents_and_files_count_a_chat_attached_document_twice(test_app):
    with test_app.app_context():
        seed_monitoring_data()

        payload = _payload(test_app)

    assert payload["totals"]["documents"] == 2
    assert payload["totals"]["files"] == 2


def test_deleting_a_session_keeps_its_activity_in_the_chart(test_app):
    with test_app.app_context():
        seed_monitoring_data()
        before = _payload(test_app)["totals"]

        delete_session_and_messages("s1", "u1", upload_folder=None)

        after = _payload(test_app)["totals"]

    assert after["sessions"] == before["sessions"]
    assert after["messages"] == before["messages"]
    assert after["files"] == before["files"]
    assert after["outgoing_tokens"] == before["outgoing_tokens"]
    assert after["incoming_tokens"] == before["incoming_tokens"]


def test_user_without_data_returns_zero_filled_buckets(test_app):
    with test_app.app_context():
        payload = _payload(test_app, range_key="1h", logins=("nobody",))

    assert len(payload["buckets"]) == 12
    assert all(value == 0 for value in payload["series"]["sessions"])
    assert all(value is None for value in payload["series"]["storage_bytes"])


def test_invalid_timezone_falls_back_to_utc(test_app):
    with test_app.app_context():
        seed_monitoring_data()
        test_app.config["TIMEZONE"] = "Not/AZone"

        payload = build_payload(["u1"], "24h", now=NOW)

    assert payload["totals"]["messages"] == 2
