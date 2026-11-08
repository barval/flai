"""A bare HTML document must be shown as code, not rendered as markup.

2026-11-09: a generated Three.js scene came back without a ```html fence. The
chat parsed it as markdown, DOMPurify removed its <script> and <style>, and the
message "became" its three control hints while the stored text was complete.
`wrapBareHtmlDocument()` puts such a reply into a fenced block first, so it stays
visible, copyable, and previewable.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHAT_JS = ROOT / "app" / "static" / "js" / "chat-messages.js"


def _wrap(text: str) -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available; behavioural JS check skipped")
    src = CHAT_JS.read_text(encoding="utf-8")
    start = src.index("function wrapBareHtmlDocument")
    end = src.index("function displayMessage")
    harness = src[start:end] + "\nprocess.stdout.write(JSON.stringify(wrapBareHtmlDocument(process.argv[1])));"
    proc = subprocess.run([node, "-e", harness, text], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node harness failed: {proc.stderr}"
    return json.loads(proc.stdout)


@pytest.mark.parametrize(
    "text",
    [
        "<!DOCTYPE html>\n<html><body><script>go()</script></body></html>",
        "<!doctype html>\n<p>lowercase doctype</p>",
        "\n  <html><body>no doctype</body></html>",
    ],
)
def test_a_bare_document_is_wrapped_in_an_html_fence(text):
    wrapped = _wrap(text)
    assert wrapped.startswith("```html\n")
    assert wrapped.endswith("\n```")
    assert text.strip() in wrapped


@pytest.mark.parametrize(
    "text",
    [
        "Обычный ответ без HTML.",
        "```html\n<!DOCTYPE html>\n```",
        "<div>фрагмент, а не документ</div>",
        "",
    ],
)
def test_everything_else_is_left_alone(text):
    assert _wrap(text) == text
