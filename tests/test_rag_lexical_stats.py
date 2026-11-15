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


def test_a_deferred_refresh_matches_a_live_count_once_rebuilt(test_app):
    """A batch writer defers the corpus pass; one rebuild at the end is enough."""
    from app.database import get_db
    from app.db import read_lexical_stats, rebuild_lexical_stats
    from modules.rag import RagModule

    with test_app.app_context():
        _cleanup()
        module = RagModule()
        module._store_chunks(USER, "doc-1", "cv.pdf", ["Работал в Твери"])
        module._store_chunks(USER, "doc-2", "cv.pdf", ["Отпуск в Сочи"], refresh=False)

        stale = read_lexical_stats(USER, ["тверь", "сочи"])
        assert stale["n_chunks"] == 1
        assert stale["df"] == {"тверь": 1}

        rebuild_lexical_stats(USER)

        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) AS n FROM rag_chunks WHERE user_id = %s", (USER,))
            live = c.fetchone()
        fresh = read_lexical_stats(USER, ["тверь", "сочи"])
        assert fresh["n_chunks"] == live["n"] == 2
        assert fresh["df"] == {"тверь": 1, "сочи": 1}
        _cleanup()


def test_the_backfill_command_writes_through_the_statistics_refresh():
    """`flask rag-backfill-chunks` used to bypass the cache and leave it stale."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "cli.py").read_text(encoding="utf-8")
    block = source[source.index("def rag_backfill_chunks(") :]
    block = block[: block.index("@click.command", 10)]

    assert "store_rag_chunks" in block
    assert "rebuild_lexical_stats" in block


def _drop_cache():
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM rag_stats WHERE user_id = %s", (USER,))
        c.execute("DELETE FROM rag_term_df WHERE user_id = %s", (USER,))


def test_a_corpus_without_lemmas_does_not_rescan_forever(test_app):
    """Every chunk empty of lemmas means avgdl is 0, and the heal used to skip it."""
    from app.db import read_lexical_stats
    from modules.rag import RagModule

    with test_app.app_context():
        _cleanup()
        _store(["   ", "!!!"])
        _drop_cache()

        assert RagModule()._lexical_search(USER, "Твери", 10) == []

        cached = read_lexical_stats(USER, ["тверь"])
        assert cached is not None
        assert cached["n_chunks"] == 2
        assert cached["avgdl"] == 0
        _cleanup()


def test_deleting_one_document_leaves_the_others_counted(test_app):
    from app.db import read_lexical_stats
    from modules.rag import RagModule

    with test_app.app_context():
        _cleanup()
        module = RagModule()
        module._store_chunks(USER, "doc-1", "one.pdf", ["Работал в Твери"])
        module._store_chunks(USER, "doc-2", "two.pdf", ["Отпуск в Сочи"])

        module._delete_chunks("doc-1", USER)

        cached = read_lexical_stats(USER, ["тверь", "сочи"])
        assert cached["n_chunks"] == 1
        assert cached["df"] == {"сочи": 1}
        _cleanup()


def test_reindexing_a_document_does_not_double_the_statistics(test_app):
    from app.db import read_lexical_stats
    from modules.rag import RagModule

    with test_app.app_context():
        _cleanup()
        module = RagModule()
        module._store_chunks(USER, "doc-1", "cv.pdf", ["Работал в Твери"])
        module._store_chunks(USER, "doc-1", "cv.pdf", ["Работал в Твери"])

        cached = read_lexical_stats(USER, ["тверь"])

        assert cached["n_chunks"] == 1
        assert cached["df"] == {"тверь": 1}
        _cleanup()


def test_a_user_without_chunks_gets_no_cache_row(test_app):
    from app.db import read_lexical_stats
    from modules.rag import RagModule

    with test_app.app_context():
        _cleanup()

        assert RagModule()._lexical_search(USER, "Твери", 10) == []
        assert read_lexical_stats(USER, ["тверь"]) is None


def test_the_cached_path_ranks_exactly_like_the_live_one(test_app):
    from modules.rag import RagModule

    with test_app.app_context():
        _cleanup()
        module = RagModule()
        module._store_chunks(USER, "doc-1", "one.pdf", ["Работал в Твери", "Тверь и отпуск"])
        module._store_chunks(USER, "doc-2", "two.pdf", ["Отпуск в Сочи", "Тверь рядом с Сочи"])

        cached = module._lexical_search(USER, "Тверь Сочи", 10)
        _drop_cache()
        live = module._lexical_search(USER, "Тверь Сочи", 10)

        assert [(r["doc_id"], r["chunk_index"], round(r["rank"], 12)) for r in cached] == [
            (r["doc_id"], r["chunk_index"], round(r["rank"], 12)) for r in live
        ]
        _cleanup()
