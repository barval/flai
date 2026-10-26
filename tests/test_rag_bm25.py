"""BM25 lexical half: lemmatization and scoring."""

import pytest

from app.lemmatizer import lemmas
from modules.rag import bm25_score

pytestmark = pytest.mark.unit


def test_lemmas_fold_russian_cases():
    assert lemmas("Москве Москва москвой") == ["москва", "москва", "москва"]


def test_lemmas_keep_latin_and_numbers():
    assert lemmas("Node2 VM 103 и storage") == ["node2", "vm", "103", "storage"]


def test_lemmas_drop_single_characters():
    assert lemmas("а и в дом") == ["дом"]


def test_bm25_weighs_rare_terms_above_common_ones():
    df = {"москва": 2, "работа": 5}
    rare = bm25_score(["москва", "работа", "работа"], ["москва", "работа"], df, 10, 3.0)
    common = bm25_score(["работа", "работа", "работа"], ["москва", "работа"], df, 10, 3.0)
    assert rare > common


def test_bm25_ignores_chunks_without_the_query_terms():
    assert bm25_score(["отпуск"], ["москва"], {"москва": 1}, 10, 3.0) == 0.0


def test_bm25_normalizes_by_chunk_length():
    df = {"москва": 1}
    short = bm25_score(["москва"], ["москва"], df, 10, 3.0)
    long = bm25_score(["москва"] + ["слово"] * 20, ["москва"], df, 10, 3.0)
    assert short > long
