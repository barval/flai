"""Tests for per-user Tavily key storage."""

import pytest

from app import tavily_keys
from app.userdb import create_user


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


@pytest.mark.unit
class TestTavilyKeyMasking:
    def test_mask_keeps_only_the_prefix_and_last_four(self):
        assert tavily_keys.mask_tavily_key("tvly-abcdefgh12345678") == "tvly-…5678"

    def test_mask_of_a_short_key_does_not_leak_it(self):
        assert tavily_keys.mask_tavily_key("tvly-abc") == "tvly-…"

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

    def test_empty_key_is_rejected(self):
        assert tavily_keys.is_valid_tavily_key_shape("   ") is False
