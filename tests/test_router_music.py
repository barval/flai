"""A music request is ordinary chat with the generate_music tool.

Measured twice on a live CPU host: the marker router answered [-REASONING-] for
"make calm music about autumn rain", and the reasoning path has no tools at all,
so no song was ever rendered. The negative exclusion in category 8 was ignored;
the positive [-MUSIC-] category is what the router follows.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modules.base import BaseModule

ROOT = Path(__file__).resolve().parent.parent

MESSAGES = {
    "ru": "сделай спокойную музыку на тему осени, дождя",
    "en": "make calm music about autumn rain",
}


@pytest.mark.unit
class TestMusicMarker:
    def _module(self):
        module = BaseModule.__new__(BaseModule)
        module.logger = MagicMock()
        return module

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_music_routes_to_chat_with_tools(self, lang):
        message = MESSAGES[lang]
        result = self._module()._parse_router_response(f"[-MUSIC-] {message}", message, "2026-11-23 12:00", lang)

        assert result["action"] == "none"
        assert result["needs_reasoning"] is False
        assert result["tools"] is None

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_whole_request_survives(self, lang):
        """The chat model chooses the tool from the words; they must reach it intact."""
        message = MESSAGES[lang]
        result = self._module()._parse_router_response(f"[-MUSIC-] {message}", message, "2026-11-23 12:00", lang)

        assert result["query"] == message

    @pytest.mark.parametrize("lang", ["ru", "en"])
    def test_the_router_prompt_carries_the_category(self, lang):
        text = (ROOT / "prompts" / lang / "base_text.template").read_text(encoding="utf-8")

        assert "[-MUSIC-]" in text
