# app/reranker.py
"""Cross-encoder reranking of the retrieved candidates.

Retrieval decides *which* passages are candidates; a cross-encoder decides their
order by reading the query and the passage together. It runs as a CPU-only
llama-server registered by ``llama_swap_config`` (no VRAM, no GPU queue slot),
so a search never waits behind a chat request.
"""

from __future__ import annotations

import logging
import math

import requests

logger = logging.getLogger(__name__)

RERANKER_MODEL = "reranker"


def rerank(
    query: str,
    documents: list[str],
    base_url: str,
    timeout: int = 10,
    model: str = RERANKER_MODEL,
) -> list[float] | None:
    """Return one relevance score per document, or ``None`` when unavailable.

    The list keeps the input order: index ``i`` of the result belongs to
    ``documents[i]``. A missing reranker (model not installed, timeout, bad
    payload) yields ``None`` and the caller keeps its own order.
    """
    if not documents:
        return []
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/rerank",
            json={"model": model, "query": query, "documents": documents},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json() or {}
    except Exception as exc:  # noqa: BLE001 — reranking is an optimisation
        logger.warning(f"rerank unavailable: {exc}")
        return None

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        logger.warning(f"rerank returned an unexpected payload: {str(payload)[:200]}")
        return None

    raw: list[float] = [0.0] * len(documents)
    for row in results:
        try:
            raw[int(row["index"])] = float(row["relevance_score"])
        except (KeyError, TypeError, ValueError, IndexError):
            logger.warning(f"rerank row ignored: {str(row)[:120]}")
            continue

    # The endpoint returns raw logits (often negative). Turn them into
    # comparable scores: sigmoid for the probability, then scale so the best
    # candidate is 1.0 — otherwise an absolute relevance threshold would drop
    # every reranked chunk.
    best = max((1.0 / (1.0 + math.exp(-value)) for value in raw), default=0.0)
    if best <= 0:
        return raw
    return [(1.0 / (1.0 + math.exp(-value))) / best for value in raw]
