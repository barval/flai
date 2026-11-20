"""An SVG pasted into the answer instead of being written as a document.

Measured: a request for an "svg-document" came back as a 13 000-character
```` ```svg ```` block. Chat renders it as code with no way to open it (the
preview button belongs to ```` ```html ````), and nothing lands in the document
library. The text is already written by the time the loop sees it, so the only
thing left is to make it the document the person asked for — the drawing is
validated and stored by the same writer the tool uses.
"""

from __future__ import annotations

import re

_BLOCK_RE = re.compile(r"```svg[ \t]*\r?\n(?P<body>.*?)```", re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(?P<title>[^<]{1,120})</title>", re.IGNORECASE)
_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def extract_svg_block(text: str) -> tuple[int, int, str] | None:
    """(start, end, markup) of the first fenced svg block, or None."""
    match = _BLOCK_RE.search(text or "")
    if not match:
        return None
    return match.start(), match.end(), match.group("body").strip()


def suggested_name(markup: str, fallback: str) -> str:
    """A file name from the drawing's own <title>, or the caller's fallback."""
    match = _TITLE_RE.search(markup or "")
    title = (match.group("title") if match else "").strip()
    title = _UNSAFE_NAME.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(" .")
    return f"{title[:80] or fallback}.svg"


_FENCE_RE = re.compile(r"```[ \t]*svg\b", re.IGNORECASE)


class SvgBlockFilter:
    """Streaming filter: forward prose, stop at a fenced svg block.

    Measured: the model narrated, then streamed the whole drawing as a ```svg
    block, and *also* called `write_document` — the user watched the markup and
    got the file. The block is not aborted (the call still needs to finish); it
    is simply not shown. The tail is held back so a fence split across two
    chunks is still caught.
    """

    HOLD = 10

    def __init__(self) -> None:
        self._pending = ""
        self.suppressed = False

    def feed(self, chunk: str) -> str:
        """The text safe to show now; empty once a drawing block has begun."""
        if self.suppressed:
            return ""
        self._pending += chunk
        match = _FENCE_RE.search(self._pending)
        if match:
            self.suppressed = True
            head = self._pending[: match.start()]
            self._pending = ""
            return head
        if len(self._pending) <= self.HOLD:
            return ""
        emit = self._pending[: -self.HOLD]
        self._pending = self._pending[-self.HOLD :]
        return emit

    def flush(self) -> str:
        """Whatever is still held back when no drawing block appeared."""
        if self.suppressed:
            return ""
        text, self._pending = self._pending, ""
        return text
