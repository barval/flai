"""BM25 corpus statistics: the cache and the numbers it holds."""

import pytest

pytestmark = pytest.mark.requires_db

USER = "lexstats"


def _cleanup():
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        for table in ("rag_chunks", "rag_stats", "rag_term_df"):
            c.execute(f"DELETE FROM {table} WHERE user_id = %s", (USER,))


def _store(chunks):
    from modules.rag import RagModule

    RagModule()._store_chunks(USER, "doc-1", "cv.pdf", chunks)


def test_the_cached_stats_match_a_live_count(test_app):
    from app.database import get_db
    from app.db import read_lexical_stats

    with test_app.app_context():
        _cleanup()
        _store(["Работал в Твери", "Отпуск в Сочи"])

        cached = read_lexical_stats(USER, ["тверь", "сочи"])

        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT count(*) AS n, coalesce(avg(cardinality(lemmas)), 0) AS avgdl"
                " FROM rag_chunks WHERE user_id = %s AND lemmas IS NOT NULL",
                (USER,),
            )
            live = c.fetchone()
        assert cached["n_chunks"] == live["n"]
        assert cached["avgdl"] == pytest.approx(float(live["avgdl"]))
        assert cached["df"] == {"тверь": 1, "сочи": 1}
        _cleanup()


def test_an_empty_chunk_does_not_break_the_average(test_app):
    from app.db import read_lexical_stats

    with test_app.app_context():
        _cleanup()
        _store(["Работал в Твери", "   "])

        cached = read_lexical_stats(USER, ["тверь"])

        assert cached["n_chunks"] == 2
        assert cached["avgdl"] == pytest.approx(1.0)
        _cleanup()


def test_deleting_the_last_document_clears_the_statistics(test_app):
    from app.db import read_lexical_stats
    from modules.rag import RagModule

    with test_app.app_context():
        _cleanup()
        _store(["Работал в Твери"])
        RagModule()._delete_chunks("doc-1", USER)

        cached = read_lexical_stats(USER, ["тверь"])

        assert cached is not None
        assert cached["n_chunks"] == 0
        assert cached["df"] == {}
        _cleanup()


def test_a_user_without_a_cache_row_gets_the_same_rows_and_a_new_row(test_app):
    from app.database import get_db
    from app.db import read_lexical_stats
    from modules.rag import RagModule

    with test_app.app_context():
        _cleanup()
        _store(["Работал в Твери", "Отпуск в Сочи"])
        cached = RagModule()._lexical_search(USER, "Твери", 10)

        with get_db() as conn:
            conn.cursor().execute("DELETE FROM rag_stats WHERE user_id = %s", (USER,))

        healed = RagModule()._lexical_search(USER, "Твери", 10)

        assert [row["doc_id"] for row in healed] == [row["doc_id"] for row in cached]
        assert read_lexical_stats(USER, ["тверь"]) is not None
        _cleanup()
