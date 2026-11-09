"""How many document fragments a retrieval may put in the prompt.

Two constants used to decide this: `top_k=20` at every reasoning call site and
`coverage_max_files=6` inside the search. Measured consequence on a real question
over 26 work logs: 20 fragments arrived, 8 of them from two unrelated PDFs, the
other 16 logs were never seen, and the coverage pass refused outright because 17
missing documents exceeded its cap of 6 — the answer looked complete and covered
10 files out of 26.

The budget is now derived from the same window split the rest of the request uses
(:mod:`app.context_budget`): the reply reserve and the measured fixed prompt are
subtracted from the window, and the rest is divided between the conversation and
the documents **by measured need** — the corpus size and the session length. A
CPU host with an 8 192-token window therefore gives a single question almost all
of the room instead of the 30 % a fixed share would hand it, and a long
conversation only takes what it actually holds.

Two things remain policy rather than resources, and are named as such:
``BREADTH_SHARE`` (how much of the fragment budget goes to covering documents the
relevance half missed) and the operator's optional ``RAG_CONTEXT_PERCENT`` cap.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import setting
from app.context_budget import cap_share, context_budget
from app.utils import estimate_tokens

# Policy: how much wider than "one chunk per document" the evidence may be — half
# again, so a small corpus is covered and the best-matching chunks get depth.
DEPTH_OVER_COVERAGE = 0.5
# Policy: with a corpus larger than the room, a quarter of the fragments goes to
# covering documents the relevance half missed; relevance keeps the rest.
BREADTH_SHARE = 0.25
# A search needs at least one relevance hit; a corpus smaller than the budget is
# covered entirely.
MIN_TOP_K = 1
# Used only when the reasoning model has no configured window (a fresh install).
DEFAULT_CONTEXT_TOKENS = 8192
# A document no larger than this is passed whole once it is the best match,
# because a question can need two of its chunks — a rule in one, its time window
# in another. Measured on the noise-law case: one relevant chunk (article 4,
# "repair works"), the hours in article 2 thirteen places down, and 96 % of the
# window unused; the whole 10-chunk law (about 1 900 tokens) answered exactly.
WHOLE_DOCUMENT_MAX_TOKENS = 2000
# The fixed prompt is measured for the request itself; this is the estimate used
# where the prompt does not exist yet (the search cap, the fast worker).
DEFAULT_FIXED_TOKENS = 800
_SAMPLE = "работа с клиентами, выполнение планов продаж, заключение договоров, поддержка серверов "


@dataclass(frozen=True)
class RetrievalBudget:
    """What one retrieval may spend, and how the room is split."""

    top_k: int
    coverage_files: int
    fragments: int
    tokens: int
    tokens_per_fragment: int
    history_tokens: int = 0


def tokens_per_fragment(app=None, lang: str = "ru", chunk_chars: int | None = None) -> int:
    """The measured cost of one fragment, label included."""
    if chunk_chars is not None:
        size = int(chunk_chars)
    elif app is not None:
        size = int(app.config.get("RAG_CHUNK_SIZE", 500))
    else:
        size = int(setting("RAG_CHUNK_SIZE", 500))
    sample = (_SAMPLE * (size // len(_SAMPLE) + 1))[: max(80, size)]
    text = ("Источник: документ.txt\n" if lang == "ru" else "Source: document.txt\n") + sample
    return max(1, estimate_tokens(text, "reasoning", lang))


def _config_value(app, key: str, default: int) -> int:
    if app is not None:
        return int(app.config.get(key, default) or default)
    return int(setting(key, default) or default)


def retrieval_budget(
    app=None,
    lang: str = "ru",
    chunk_chars: int | None = None,
    history_need: int = 0,
    corpus_documents: int | None = None,
) -> RetrievalBudget:
    """The fragment budget for one request.

    ``app`` may be None (a call outside a request context): the numbers then come
    from the environment, which is where the app reads them anyway.
    ``corpus_documents`` is how many indexed documents the request can search;
    when it is unknown the documents may use all of the room.
    """
    if app is not None:
        from app.model_config import get_model_config

        config = get_model_config("reasoning") or {}
        window = int(config.get("context_length") or DEFAULT_CONTEXT_TOKENS)
        fixed = int(app.config.get("TEMPLATE_OVERHEAD_TOKENS", DEFAULT_FIXED_TOKENS) or DEFAULT_FIXED_TOKENS)
    else:
        window = DEFAULT_CONTEXT_TOKENS
        fixed = int(setting("TEMPLATE_OVERHEAD_TOKENS", DEFAULT_FIXED_TOKENS))
    per_fragment = tokens_per_fragment(app, lang, chunk_chars)
    budget = context_budget(window, fixed)

    documents_need = (
        int(int(corpus_documents) * per_fragment * (1 + DEPTH_OVER_COVERAGE))
        if corpus_documents
        else budget.available
    )
    if corpus_documents:
        # A small corpus can still need one whole document: a rule and its time
        # window live in different chunks, and a single relevance chunk cannot
        # answer. The floor is what one small document costs, so the expansion
        # has somewhere to put it without stealing the conversation's room.
        documents_need = max(documents_need, min(budget.available, WHOLE_DOCUMENT_MAX_TOKENS))
    documents_tokens, history_tokens = budget.split(history_need=history_need, document_need=documents_need)
    documents_tokens = cap_share(documents_tokens, budget.available, _config_value(app, "RAG_CONTEXT_PERCENT", 0))

    fragments = max(MIN_TOP_K, documents_tokens // per_fragment)
    corpus = max(0, int(corpus_documents or 0))
    if corpus and corpus <= fragments:
        # Everything fits: represent every document and spend the rest on depth.
        coverage = corpus
        top_k = max(MIN_TOP_K, fragments - corpus)
    else:
        coverage = min(corpus, max(1, int(fragments * BREADTH_SHARE))) if corpus else 0
        top_k = max(MIN_TOP_K, fragments - coverage)
    # An explicit RAG_TOP_K narrows the relevance half; it cannot widen it past
    # what the window holds.
    configured = _config_value(app, "RAG_TOP_K", 0)
    if configured > 0:
        top_k = min(top_k, configured)
    return RetrievalBudget(
        top_k=top_k,
        coverage_files=coverage,
        fragments=fragments,
        tokens=documents_tokens,
        tokens_per_fragment=per_fragment,
        history_tokens=history_tokens,
    )
