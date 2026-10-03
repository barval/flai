"""Tests for per-user Tavily key storage."""

from unittest.mock import MagicMock, patch

import pytest
import requests as real_requests
from flask import Flask

from app import config as config_mod
from app import tavily_keys
from app.userdb import create_user


def _recorded_set(login, key):
    """Run set_tavily_key against a recording connection, return (sql, params)."""
    conn = MagicMock()
    with patch("app.tavily_keys.get_db") as get_db:
        get_db.return_value.__enter__.return_value = conn
        tavily_keys.set_tavily_key(login, key)
    return conn.cursor.return_value.execute.call_args.args


def _tavily_enabled(monkeypatch, raw):
    """Load the config with TAVILY_ENABLED set to raw (None = unset)."""
    monkeypatch.setenv("SECRET_KEY", "tavily-flag-test")
    if raw is None:
        monkeypatch.delenv("TAVILY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("TAVILY_ENABLED", raw)
    flask_app = Flask(__name__)
    config_mod.load_config(flask_app)
    return flask_app.config["TAVILY_ENABLED"]


@pytest.fixture
def tavily_user(test_app):
    create_user(login="tavilyuser", password="pw-tavilyuser-123", name="Tavily User")
    return "tavilyuser"


@pytest.mark.unit
class TestTavilyKeyStorage:
    def test_absent_key_reads_as_none(self, test_app, tavily_user):
        assert tavily_keys.get_tavily_key(tavily_user) is None

    def test_set_then_get_round_trips(self, test_app, tavily_user):
        tavily_keys.set_tavily_key(tavily_user, "tvly-abcdefgh12345678")

        assert tavily_keys.get_tavily_key(tavily_user) == "tvly-abcdefgh12345678"

    def test_delete_clears_the_key(self, test_app, tavily_user):
        tavily_keys.set_tavily_key(tavily_user, "tvly-abcdefgh12345678")

        tavily_keys.delete_tavily_key(tavily_user)

        assert tavily_keys.get_tavily_key(tavily_user) is None

    def test_empty_string_is_treated_as_absent(self, test_app, tavily_user):
        tavily_keys.set_tavily_key(tavily_user, "")

        assert tavily_keys.get_tavily_key(tavily_user) is None

    def test_empty_key_clears_the_stored_timestamp(self):
        sql, params = _recorded_set("tavilyuser", "")

        assert " ".join(sql.split()) == (
            "UPDATE users SET tavily_api_key = %s, tavily_key_added_at = %s, "
            "updated_at = CURRENT_TIMESTAMP WHERE login = %s"
        )
        assert params == (None, None, "tavilyuser")

    def test_stored_key_stamps_the_addition_time(self):
        sql, params = _recorded_set("tavilyuser", "tvly-abcdefgh12345678")

        assert " ".join(sql.split()) == (
            "UPDATE users SET tavily_api_key = %s, tavily_key_added_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE login = %s"
        )
        assert params == ("tvly-abcdefgh12345678", "tavilyuser")


@pytest.mark.unit
class TestTavilyKeyMasking:
    def test_mask_keeps_only_the_prefix_and_last_four(self):
        assert tavily_keys.mask_tavily_key("tvly-abcdefgh12345678") == "tvly-…5678"

    def test_mask_of_a_short_key_does_not_leak_it(self):
        assert tavily_keys.mask_tavily_key("tvly-abc") == "tvly-…"

    def test_mask_of_a_nine_char_key_does_not_leak_it(self):
        assert tavily_keys.mask_tavily_key("tvly-abcd") == "tvly-…"

    def test_mask_of_empty_key_is_empty(self):
        assert tavily_keys.mask_tavily_key("") == ""


@pytest.mark.unit
class TestTavilyKeyShape:
    def test_prefixed_key_is_accepted(self):
        assert tavily_keys.is_valid_tavily_key_shape("tvly-abcdefgh12345678") is True

    def test_other_prefix_is_rejected(self):
        assert tavily_keys.is_valid_tavily_key_shape("sk-abcdefgh12345678") is False

    def test_overlong_key_is_rejected(self):
        assert tavily_keys.is_valid_tavily_key_shape("tvly-" + "a" * 200) is False

    def test_key_of_exactly_the_max_length_is_accepted(self):
        at_limit = tavily_keys.KEY_PREFIX + "a" * (tavily_keys.KEY_MAX_LENGTH - len(tavily_keys.KEY_PREFIX))

        assert tavily_keys.is_valid_tavily_key_shape(at_limit) is True

    def test_key_one_char_over_the_limit_is_rejected(self):
        over_limit = tavily_keys.KEY_PREFIX + "a" * (tavily_keys.KEY_MAX_LENGTH - len(tavily_keys.KEY_PREFIX) + 1)

        assert tavily_keys.is_valid_tavily_key_shape(over_limit) is False

    def test_surrounding_whitespace_is_stripped(self):
        assert tavily_keys.is_valid_tavily_key_shape("  tvly-abc  ") is True

    def test_empty_key_is_rejected(self):
        assert tavily_keys.is_valid_tavily_key_shape("   ") is False


@pytest.mark.unit
class TestTavilyEnabledFlag:
    def test_enabled_by_default(self, monkeypatch):
        assert _tavily_enabled(monkeypatch, None) is True

    @pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes"])
    def test_accepted_true_values_enable_the_flag(self, monkeypatch, raw):
        assert _tavily_enabled(monkeypatch, raw) is True

    @pytest.mark.parametrize("raw", ["false", "0", "no", "off", "disabled", "none", "", "maybe"])
    def test_everything_else_fails_closed(self, monkeypatch, raw):
        assert _tavily_enabled(monkeypatch, raw) is False


@pytest.mark.unit
class TestFetchTavilyUsage:
    @staticmethod
    def _response(status_code=200, payload=None):
        class _Resp:
            def __init__(self):
                self.status_code = status_code

            def json(self):
                if payload is None:
                    raise ValueError("no json")
                return payload

        return _Resp()

    def test_no_key_short_circuits(self):
        assert tavily_keys.fetch_tavily_usage("", "https://api.tavily.com", 8)["status"] == "no_key"

    def test_remaining_credits_are_reported(self, test_app):
        payload = {"key": {"usage": 120, "limit": 1000}, "account": {"current_plan": "Free"}}
        with patch("app.tavily_keys.requests") as mock_requests:
            mock_requests.get.return_value = self._response(200, payload)

            usage = tavily_keys.fetch_tavily_usage("tvly-key", "https://api.tavily.com", 8)

        assert usage == {
            "status": "ok",
            "plan": "Free",
            "limit": 1000,
            "used": 120,
            "remaining": 880,
        }

    def test_spent_quota_is_exhausted(self, test_app):
        payload = {"key": {"usage": 1000, "limit": 1000}}
        with patch("app.tavily_keys.requests") as mock_requests:
            mock_requests.get.return_value = self._response(200, payload)

            usage = tavily_keys.fetch_tavily_usage("tvly-key", "https://api.tavily.com", 8)

        assert usage["status"] == "exhausted"
        assert usage["remaining"] == 0

    def test_missing_key_fields_fall_back_to_account_totals(self, test_app):
        payload = {"key": {}, "account": {"plan_usage": 10, "plan_limit": 1000}}
        with patch("app.tavily_keys.requests") as mock_requests:
            mock_requests.get.return_value = self._response(200, payload)

            usage = tavily_keys.fetch_tavily_usage("tvly-key", "https://api.tavily.com", 8)

        assert (usage["limit"], usage["used"], usage["remaining"]) == (1000, 10, 990)

    def test_unauthorized_is_invalid(self, test_app):
        with patch("app.tavily_keys.requests") as mock_requests:
            mock_requests.get.return_value = self._response(401)

            usage = tavily_keys.fetch_tavily_usage("tvly-key", "https://api.tavily.com", 8)

        assert usage["status"] == "invalid"

    def test_transport_error_is_unavailable(self, test_app):
        with patch("app.tavily_keys.requests") as mock_requests:
            mock_requests.get.side_effect = real_requests.ConnectionError("boom")

            usage = tavily_keys.fetch_tavily_usage("tvly-key", "https://api.tavily.com", 8)

        assert usage["status"] == "unavailable"

    def test_server_error_is_unavailable(self, test_app):
        with patch("app.tavily_keys.requests") as mock_requests:
            mock_requests.get.return_value = self._response(503)

            usage = tavily_keys.fetch_tavily_usage("tvly-key", "https://api.tavily.com", 8)

        assert usage["status"] == "unavailable"

    def test_non_json_body_is_unavailable(self, test_app):
        with patch("app.tavily_keys.requests") as mock_requests:
            mock_requests.get.return_value = self._response(200)

            usage = tavily_keys.fetch_tavily_usage("tvly-key", "https://api.tavily.com", 8)

        assert usage["status"] == "unavailable"
