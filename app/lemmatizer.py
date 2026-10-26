"""Russian lemmatization for the lexical half of hybrid search.

``ts_rank_cd`` had neither IDF nor length normalisation, and the pre-stemmed
``tsvector`` could not express BM25. The lexical half therefore stores one lemma
per word and scores it with BM25 in Python: measured on two 30-question sets the
fused MRR rose from 0.688/0.653 to 0.802/0.811 and nDCG@20 from 0.765/0.742 to
0.872.

Latin words and numbers are kept as-is (lowercased); only Cyrillic goes through
the morphological analyser, which is cached per word because the same words
repeat across chunks.
"""

from __future__ import annotations

import re
from functools import lru_cache

_WORD_RE = re.compile(r"[a-zа-яё0-9]+")
_CYRILLIC_RE = re.compile(r"[а-яё]")


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=200_000)
def _lemma(word: str) -> str:
    return _morph().parse(word)[0].normal_form


def lemmas(text: str) -> list[str]:
    """Content words of ``text`` reduced to their dictionary form."""
    result: list[str] = []
    for word in _WORD_RE.findall((text or "").lower()):
        if len(word) < 2:
            continue
        result.append(_lemma(word) if _CYRILLIC_RE.search(word) else word)
    return result
