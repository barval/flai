"""The TTS splitter must cut sentences, not numbers, URLs and quotes.

Regression (2026-11-05): `splitIntoSentences()` in chat-tts.js ended a sentence at
any dot, because the terminator was followed by `\\s*` instead of a real boundary.
A version number reached the synthesizer as four fragments, a decimal as two, a
domain as two, and a closing quote opened the next chunk — the answer was spoken
chopped up.

The function is checked in node, like `test_reasoning_strip_js.py`: the browser
code is what runs, not a Python copy of it.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TTS_JS = ROOT / "app" / "static" / "js" / "chat-tts.js"


def _run(expression: str, text: str, *, segmenter: bool = True) -> object:
    """Evaluate the REAL splitter functions on ``text`` in node."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available; behavioural JS check skipped")
    src = TTS_JS.read_text(encoding="utf-8")
    start = src.index("const NON_TERMINAL_ABBREVIATIONS")
    end = src.index("function resetTtsState")
    # The fallback is what a browser without Intl.Segmenter runs; deleting the
    # constructor is the only way to exercise it here.
    prelude = "" if segmenter else "delete Intl.Segmenter;\n"
    harness = f"{prelude}{src[start:end]}\nprocess.stdout.write(JSON.stringify({expression}));"
    proc = subprocess.run([node, "-e", harness, text], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"node harness failed: {proc.stderr}"
    return json.loads(proc.stdout)


def _split(text: str, *, segmenter: bool = True) -> list[str]:
    return _run("splitIntoSentences(process.argv[1])", text, segmenter=segmenter)


def _strip_code(text: str, *, segmenter: bool = True) -> str:
    return _run("stripCodeBlocksForTTS(process.argv[1])", text, segmenter=segmenter)


BOTH_PATHS = pytest.mark.parametrize("segmenter", [True, False], ids=["segmenter", "fallback"])


@BOTH_PATHS
@pytest.mark.parametrize(
    "text,expected",
    [
        # A dot inside a number is not a sentence end.
        ("3.14 — это число.", ["3.14 — это число."]),
        # Neither is the dot of a domain.
        ("example.com — сайт.", ["example.com — сайт."]),
        # The closing quote belongs to the sentence that ended.
        ("«Привет!» Как дела?", ["«Привет!»", "Как дела?"]),
        # A two-letter Russian abbreviation is one token: the old list only
        # knew the Latin forms.
        ("т.е. это пример.", ["т.е. это пример."]),
        # A version number keeps its dots, and the real boundary still cuts.
        ("Версия 1.2.3. Готово.", ["Версия 1.2.3.", "Готово."]),
        # Ordinary text still splits.
        ("Первое. Второе! Третье?", ["Первое.", "Второе!", "Третье?"]),
        # An ellipsis is a pause, not an end: ICU does not break there either,
        # and splitting would cut a sentence in half at its pause.
        ("Это многоточие… И продолжение.", ["Это многоточие… И продолжение."]),
        # Nothing to split.
        ("", []),
    ],
)
def test_sentences_are_split_at_real_boundaries_only(text, expected, segmenter):
    assert _split(text, segmenter=segmenter) == expected


@BOTH_PATHS
def test_no_text_is_lost(segmenter):
    """Fragments are spoken, so they must add up to the original words."""
    text = "Версия 1.2.3. Готово. Цена 3.14 — это число, см. example.com."
    assert " ".join(_split(text, segmenter=segmenter)) == text


@BOTH_PATHS
def test_a_fenced_code_block_is_never_spoken(segmenter):
    text = "Вот код: ```python\nprint('3.14')\n``` Готово."
    stripped = _strip_code(text, segmenter=segmenter)
    assert "print" not in stripped
    assert "```" not in stripped
    assert _split(stripped, segmenter=segmenter) == ["Вот код: Готово."]


@BOTH_PATHS
def test_a_half_cut_fence_drops_the_rest(segmenter):
    """A sentence boundary can cut a fence in half; the half is still code."""
    stripped = _strip_code("Текст. ```python\nне закрыт\nещё код", segmenter=segmenter)
    assert "не закрыт" not in stripped
    assert _split(stripped, segmenter=segmenter) == ["Текст."]


@BOTH_PATHS
def test_a_tilde_fence_is_removed_too(segmenter):
    assert "code" not in _strip_code("До. ~~~\ncode\n~~~ После.", segmenter=segmenter)
