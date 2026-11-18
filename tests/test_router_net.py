"""A network check is chat with tools — never search, never reasoning.

A search engine returns articles about how to check a network; a reasoning answer
cannot measure anything. Both were measured on the live instance: the marker router
answered "[-REASONING-]" and the kernel answered "search" for "measure the speed to
<host>", and both paths hand the tools to nobody.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modules.base import BaseModule

ROOT = Path(__file__).resolve().parent.parent

MESSAGES = {
    "ru": [
        "измерь скорость до server-mc.prits.ru",
        "проверь пинг до mail.ru",
        "кто владелец домена prits.ru",
        "какие порты открыты на server-mc.prits.ru",
    ],
    "en": [
        "measure the speed to server-mc.prits.ru",
        "who owns prits.ru",
        "which ports are open on server-mc.prits.ru",
    ],
}


@pytest.mark.unit
class TestNetworkMarker:
    def _module(self):
        module = BaseModule.__new__(BaseModule)
        module.logger = MagicMock()
        return module

    @pytest.mark.parametrize("message", MESSAGES["ru"] + MESSAGES["en"])
    def test_the_net_marker_routes_to_chat_with_tools(self, message):
        module = self._module()

        result = module._parse_router_response(f"[-NET-] {message}", message, "2026-11-18 09:00", "ru")

        assert result["action"] == "none"
        assert result["needs_reasoning"] is False
        assert result["tools"] is None

    @pytest.mark.parametrize("message", MESSAGES["ru"] + MESSAGES["en"])
    def test_the_whole_request_survives_not_just_the_target(self, message):
        """The chat model picks the tool from the user's words, so the words must arrive."""
        module = self._module()

        result = module._parse_router_response("[-NET-] prits.ru", message, "2026-11-18 09:00", "ru")

        assert result["query"] == message

    def test_search_is_still_search(self):
        module = self._module()

        result = module._parse_router_response("[-SEARCH-] курс биткоина", "курс биткоина", "2026-11-18 09:00", "ru")

        assert result["action"] == "search"


@pytest.mark.unit
class TestNetworkRules:
    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_marker_template_carries_the_network_category(self, lang):
        text = (ROOT / f"prompts/{lang}/base_text.template").read_text(encoding="utf-8")

        assert "[-NET-]" in text
        assert "iperf3" in text

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_kernel_rule_names_measurement_and_forbids_search(self, lang):
        from modules.base import _KERNEL_NETWORK_RULES

        rule = _KERNEL_NETWORK_RULES[lang]

        assert "net_" in rule
        assert "search" in rule.lower()
        assert "iperf3" in rule
