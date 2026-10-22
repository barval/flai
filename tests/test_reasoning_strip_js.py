# tests/test_reasoning_strip_js.py
"""The client's `_stripGenericReasoning` must never cut a structured answer.

The browser strips plain-text chain-of-thought from a streamed answer before
rendering it (the JS mirror of the server's `_strip_generic_reasoning`).
Regression (2026-10-16): both layers treated any answer carrying two
"Analyze …" / "Review …" marker phrases as chain-of-thought and dropped
everything before the last marker, which sliced JSON answers (task plans,
verifier verdicts) and ordinary replies.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVENTS_JS = ROOT / "app" / "static" / "js" / "events.js"


def _strip_in_node(text: str) -> str:
    """Run the REAL events.js `_stripGenericReasoning` on ``text`` in node."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available; behavioural JS check skipped")
    src = EVENTS_JS.read_text(encoding="utf-8")
    start = src.index("const _REASONING_MARKERS_RE")
    end = src.index("// -- stream_token")
    harness = src[start:end] + "\nprocess.stdout.write(_stripGenericReasoning(process.argv[1]));"
    proc = subprocess.run([node, "-e", harness, text], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node harness failed: {proc.stderr}"
    return proc.stdout


def test_json_answer_is_not_stripped():
    plan = json.dumps(
        {
            "title": "Analyze attached images for similarities and differences",
            "steps": [
                {"goal": "Extract what is visible in the first image", "tool": "vision"},
                {"goal": "Identify commonalities and differences between the four images", "tool": "llm"},
            ],
        }
    )
    assert _strip_in_node(plan) == plan


def test_plain_text_cot_is_still_stripped():
    cot = (
        "Let me review the user request carefully. "
        "The user asked to fix the white screen. "
        "Final Answer: the page now renders correctly because the missing container rule was restored "
        "and every panel is visible again after the fix lands in production."
    )
    out = _strip_in_node(cot)
    assert "Let me review" not in out
    assert "the page now renders correctly" in out
