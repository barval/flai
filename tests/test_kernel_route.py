"""The route adapter must reproduce the marker parser's quirks exactly.

Every downstream caller (queue, chat, media) was written against the dict that
``_parse_router_response`` returns. The kernel reuses that shape on purpose, so
these tests pin the behaviour that made the marker parser what it is.
"""

from __future__ import annotations

import pytest

from app.kernel import KernelDecision, SourceFlags, decision_to_route


def _decision(intent: str, query: str = "", tools: tuple[str, ...] = ()) -> KernelDecision:
    return KernelDecision(intent=intent, query=query, sources=SourceFlags(), tools=tools, confidence=1.0)


class _FakeCam:
    room_names = {"gos", "kab"}

    def get_room_code(self, text: str) -> str | None:
        return "gos" if "living" in text else None


@pytest.mark.unit
def test_image_uses_the_original_query():
    # The marker path ignores text after [-IMAGE-] because models echo history there.
    route = decision_to_route(_decision("image", "a red cat"), "draw a blue dog", cam=None)
    assert {key: route[key] for key in ("action", "query", "needs_reasoning")} == {
        "action": "image",
        "query": "draw a blue dog",
        "needs_reasoning": False,
    }


@pytest.mark.unit
def test_video_uses_the_original_query():
    route = decision_to_route(_decision("video", "a clip"), "make a video of rain", cam=None)
    assert route["query"] == "make a video of rain"


@pytest.mark.unit
def test_camera_keeps_a_room_code():
    route = decision_to_route(_decision("camera", "gos"), "show the living room", cam=None)
    assert route["query"] == "gos"


@pytest.mark.unit
def test_camera_falls_back_to_the_original_query_when_empty():
    route = decision_to_route(_decision("camera", ""), "покажи гостиную", cam=None)
    assert route["query"] == "покажи гостиную"


@pytest.mark.unit
def test_camera_resolves_a_sentence_to_a_room_code():
    route = decision_to_route(_decision("camera", "show me the living room"), "покажи гостиную", cam=_FakeCam())
    assert route["query"] == "gos"


@pytest.mark.unit
def test_overlong_query_falls_back_to_the_original():
    route = decision_to_route(_decision("search", "x" * 200), "погода", cam=None)
    assert route["query"] == "погода"


@pytest.mark.unit
def test_reasoning_web_sets_needs_reasoning():
    assert decision_to_route(_decision("reasoning_web", "trends"), "q", cam=None)["needs_reasoning"] is True


@pytest.mark.unit
def test_plain_reasoning_sets_needs_reasoning():
    assert decision_to_route(_decision("reasoning", "code"), "q", cam=None)["needs_reasoning"] is True


@pytest.mark.unit
def test_none_returns_the_original_query():
    route = decision_to_route(_decision("none"), "привет", cam=None)
    assert {key: route[key] for key in ("action", "query", "needs_reasoning")} == {
        "action": "none",
        "query": "привет",
        "needs_reasoning": False,
    }


@pytest.mark.unit
def test_the_decision_carries_its_tools_alongside_the_route():
    # An empty tuple means "the kernel had no opinion"; the chat path must then
    # advertise every tool, which is why the value is None rather than ().
    route = decision_to_route(_decision("rag", "contract", ("rag_search",)), "q", cam=None)
    assert route["tools"] == ("rag_search",)
    assert decision_to_route(_decision("rag", "contract"), "q", cam=None)["tools"] is None
