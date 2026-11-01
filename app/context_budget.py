"""How the context window is split between evidence, history and the answer.

Both halves of this were fixed proportions: documents took 30 % of the window and
history took 75 % of it, multiplied by a safety margin — so a CPU host with an
8 192-token window gave documents 2 458 tokens *because the number said 30 %*,
not because that was what fitted, and a session with three short messages paid the
same history tax as a fifty-message one. The proportions were also multiplied
together (75 % × 85 % × 30 %) in the search cap, leaving ~19 % for search content
by accident rather than by decision.

The allocator below is need-driven:

* the reply gets a reserve proportional to the window (a 4k reply in an 8k window
  is as wrong as 200 tokens in a 32k one);
* the fixed prompt is measured, not assumed;
* documents and history split what is left **by what they actually need** — the
  corpus size and the session length are measured before anything is granted;
* each side keeps a floor, so a huge corpus cannot silence the conversation and a
  long conversation cannot silence the documents.

Both ``RAG_CONTEXT_PERCENT`` and ``CONTEXT_HISTORY_PERCENT`` become optional caps
(0 = automatic): an operator can still pin a share, but the default is derived.
"""

from __future__ import annotations

from dataclasses import dataclass

# The reply needs room in proportion to the window, bounded at both ends: a tiny
# window cannot spare a big answer, and a huge one does not need a fourth of it.
ANSWER_SHARE = 0.10
MIN_ANSWER_TOKENS = 512
MAX_ANSWER_TOKENS = 4096
# History keeps at least this much when the evidence would otherwise take it all.
MIN_HISTORY_TOKENS = 256
# Evidence keeps at least this share of the room when the conversation is long.
MIN_DOCUMENT_SHARE = 0.5


@dataclass(frozen=True)
class ContextBudget:
    """The room a request has, and how it is divided by need."""

    window: int
    fixed: int
    answer: int
    available: int

    def split(self, history_need: int = 0, document_need: int = 0) -> tuple[int, int]:
        """``(documents, history)`` in tokens, never exceeding ``available``.

        ``document_need`` may be an estimate (corpus size x fragment cost) and may
        be larger than the room; ``history_need`` is what the session actually
        holds. When both fit, each gets what it needs and the rest goes to the
        documents, which can always use one more fragment.
        """
        available = max(0, self.available)
        if available == 0:
            return 0, 0
        history_need = max(0, int(history_need))
        document_need = max(0, int(document_need))
        if history_need + document_need <= available:
            # Both needs fit: grant them, leave the rest unused. Filling the
            # window because it is there is how a request pays for fragments
            # nobody asked for.
            return document_need, history_need
        history_floor = min(history_need, max(MIN_HISTORY_TOKENS, available // 8))
        total = history_need + document_need
        proportional = int(available * document_need / total) if total else available
        documents = min(available, max(proportional, int(available * MIN_DOCUMENT_SHARE)))
        documents = min(documents, available - history_floor)
        history = history_need if available - documents >= history_need else available - documents
        return max(0, documents), max(0, history)


def answer_reserve(window: int) -> int:
    """Room kept for the reply, proportional to the window."""
    return max(MIN_ANSWER_TOKENS, min(MAX_ANSWER_TOKENS, int(window * ANSWER_SHARE)))


def context_budget(window: int, fixed_tokens: int = 0, application=0) -> ContextBudget:
    """Build the budget for one window.

    ``application`` is 0 when there is no app to ask for a manual cap; a positive
    value pins the reply reserve instead of deriving it.
    """
    window = max(1, int(window))
    answer = int(application) if application and application > 0 else answer_reserve(window)
    answer = min(answer, max(1, window // 2))
    fixed = max(0, int(fixed_tokens))
    available = max(0, window - fixed - answer)
    return ContextBudget(window=window, fixed=fixed, answer=answer, available=available)


def cap_share(tokens: int, available: int, percent: int) -> int:
    """Apply an operator's manual cap; 0 or less means "automatic"."""
    if percent <= 0:
        return tokens
    return min(tokens, int(available * min(100, percent) / 100))
