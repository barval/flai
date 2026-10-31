"""How many document fragments a retrieval may put in the prompt, from the window.

The two numbers that decide retrieval breadth — how many fragments to fetch and
how many uncovered documents to add — used to be constants (`top_k=20` at the call
sites, `coverage_max_files=6` in the search). Measured consequence on a real
question over 26 work logs: 20 fragments arrived, 8 of them came from two
unrelated PDFs, the other 16 logs were never seen, and the coverage pass refused
outright because 17 missing documents exceeded its cap of 6 — so the answer
looked complete and covered 10 files out of 26.

The budget is derived from the answering model's context window, which the
deployment already auto-fits to the card (24 576 on a 16 GB GPU, 8 192 on CPU):

    fragments = window x RAG_CONTEXT_PERCENT / tokens_per_fragment

``tokens_per_fragment`` is measured with the project's own token estimator, so a
Russian fragment costs what it really costs (~260 tokens for a 500-character
chunk) and an English one less. Relevance keeps the larger share of the budget
and coverage gets the rest, ranked by the same search, so the breadth grows with
the hardware instead of staying a magic number.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.utils import estimate_tokens

# The query itself is what the user asked about: relevance gets most of the room.
RELEVANCE_SHARE = 0.7
# Below this a search is not a search any more; the prompt still has room because
# the caller trims by the same budget later.
MIN_TOP_K = 5
# No installation needs more than this many fragments no matter how big the card.
MAX_COVERAGE_FILES = 40
# Used only when the reasoning model has no configured window (a fresh install).
DEFAULT_CONTEXT_TOKENS = 8192
# A fragment carries its source label in front of the text.
SOURCE_LABEL_CHARS = 24
_SAMPLE = "работа с клиентами, выполнение планов продаж, заключение договоров, поддержка серверов "


@dataclass(frozen=True)
class RetrievalBudget:
    """What one retrieval may spend, and how the room is split."""

    top_k: int
    coverage_files: int
    fragments: int
    tokens: int
    tokens_per_fragment: int


def tokens_per_fragment(app=None, lang: str = "ru", chunk_chars: int | None = None) -> int:
    """The measured cost of one fragment, label included."""
    if chunk_chars is not None:
        size = int(chunk_chars)
    elif app is not None:
        size = int(app.config.get("RAG_CHUNK_SIZE", 500))
    else:
        size = int(os.getenv("RAG_CHUNK_SIZE", "500"))
    sample = (_SAMPLE * (size // len(_SAMPLE) + 1))[: max(80, size)]
    text = ("Источник: документ.txt\n" if lang == "ru" else "Source: document.txt\n") + sample
    return max(1, estimate_tokens(text, "reasoning", lang))


def retrieval_budget(app=None, lang: str = "ru", chunk_chars: int | None = None) -> RetrievalBudget:
    """The fragment budget for this installation, derived from its window.

    ``app`` may be None (a call outside a request context, e.g. a CLI path or a
    test): the numbers then come from the environment, which is where the app
    would have read them anyway.
    """
    if app is not None:
        from app.model_config import get_model_config

        config = get_model_config("reasoning") or {}
        window = int(config.get("context_length") or DEFAULT_CONTEXT_TOKENS)
        share = int(app.config.get("RAG_CONTEXT_PERCENT", 30))
        configured = int(app.config.get("RAG_TOP_K", 80) or 80)
        chunk = int(chunk_chars or app.config.get("RAG_CHUNK_SIZE", 500))
    else:
        window = DEFAULT_CONTEXT_TOKENS
        share = int(os.getenv("RAG_CONTEXT_PERCENT", "30"))
        configured = int(os.getenv("RAG_TOP_K", "80") or 80)
        chunk = int(chunk_chars or os.getenv("RAG_CHUNK_SIZE", "500"))
    share = max(10, min(90, share))
    tokens = int(window * share / 100)
    per_fragment = tokens_per_fragment(app, lang, chunk)
    fragments = max(MIN_TOP_K, tokens // per_fragment)
    top_k = min(configured, max(MIN_TOP_K, int(fragments * RELEVANCE_SHARE)))
    coverage = min(MAX_COVERAGE_FILES, max(0, fragments - top_k))
    return RetrievalBudget(
        top_k=top_k,
        coverage_files=coverage,
        fragments=fragments,
        tokens=tokens,
        tokens_per_fragment=per_fragment,
    )
