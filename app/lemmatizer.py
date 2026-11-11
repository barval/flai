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


# English has no analyser in this stack (pymorphy is Russian), so the graph
# keeps its English keys with a deterministic suffix rule set instead. Measured
# before the change: "supplies", "supplied" and "supply" were three different
# terms, so an English document's relations could never merge with its own
# entities, let alone another document's.
_EN_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those", "was",
    "were", "are", "his", "her", "its", "their", "has", "have", "had", "been",
    "will", "would", "can", "could", "should", "into", "about", "over", "under",
    "to", "of", "in", "on", "at", "by", "as", "is", "it", "be", "we", "or", "an",
    "not", "but", "all", "any", "who", "what", "when", "how",
}


@lru_cache(maxsize=200_000)
def _stem_en(word: str) -> str:
    """Fold English inflection onto one form: supply/supplies/supplied."""
    if len(word) <= 3 or not word.isalpha():
        return word
    if word.endswith(("ies", "ied")) and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("sses", "shes", "ches", "xes", "zes")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    stripped = False
    if word.endswith("ing") and len(word) >= 6:
        word, stripped = word[:-3], True
    elif word.endswith("ed") and len(word) >= 5:
        word, stripped = word[:-2], True
    # Collapsing a doubled consonant is only safe when a suffix was just
    # removed: "running" -> "runn" -> "run", but "business" must stay whole.
    if stripped and len(word) >= 4 and word[-1] == word[-2] and word[-1] not in "aeiou":
        word = word[:-1]
    if len(word) >= 6 and word.endswith("e"):
        word = word[:-1]
    return word


def lemmas(text: str, stem_latin: bool = False) -> list[str]:
    """Content words of ``text`` reduced to their dictionary form.

    ``stem_latin`` folds English words onto one form and drops English
    stopwords. It is off by default: the stored lexical index of an existing
    corpus was built without it, and changing it in place would stop old chunks
    from matching new queries.
    """
    result: list[str] = []
    for word in _WORD_RE.findall((text or "").lower()):
        if len(word) < 2:
            continue
        if _CYRILLIC_RE.search(word):
            result.append(_lemma(word))
            continue
        if stem_latin:
            if word in _EN_STOPWORDS:
                continue
            result.append(_stem_en(word))
        else:
            result.append(word)
    return result
