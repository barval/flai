"""Retrieval hot spots, measured on the same corpus with and without the cache.

Run against a real database (``DATABASE_URL``); the script writes its own
synthetic rows under one user and removes them in a ``finally`` block.

    python -m bench.lexical stats --chunks 1000
    python -m bench.lexical stats --chunks 10000
    python -m bench.lexical store --chunks 300
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time

WORDS = [
    "закон", "ремонт", "тишина", "час", "ночь", "договор", "работа", "Тверь",
    "отпуск", "проект", "смета", "встреча", "документ", "запрос", "ответ",
    "сервер", "резерв", "копия", "доступ", "ключ", "проверка", "план",
]


def _user() -> str:
    return f"bench_lexical_{os.getpid()}"


def _scratch_rows(user: str, chunks: int) -> list[tuple]:
    """Synthetic chunks: enough distinct lemmas to look like a real corpus."""
    rng = random.Random(7)
    rows = []
    for index in range(chunks):
        lemmas = [rng.choice(WORDS) for _ in range(rng.randint(30, 90))]
        rows.append((user, "doc-1", index, "bench.txt", " ".join(lemmas), lemmas))
    return rows


def _insert(rows: list[tuple]) -> None:
    from app.database import get_db

    with get_db() as conn:
        conn.cursor().executemany(
            "INSERT INTO rag_chunks (user_id, doc_id, chunk_index, filename, text, lemmas)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            rows,
        )


def _cleanup(user: str) -> None:
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        for table in ("rag_chunks", "rag_stats", "rag_term_df"):
            c.execute(f"DELETE FROM {table} WHERE user_id = %s", (user,))


def _ms(values: list[float]) -> tuple[float, float]:
    p50 = statistics.median(values)
    p95 = statistics.quantiles(values, n=20)[18] if len(values) > 5 else max(values)
    return p50, p95


def cmd_stats(chunks: int, runs: int) -> None:
    from app.db import read_lexical_stats, rebuild_lexical_stats
    from modules.rag import RagModule

    user = _user()
    terms = ["закон", "ремонт", "тишина"]
    try:
        _cleanup(user)
        _insert(_scratch_rows(user, chunks))
        rebuild_lexical_stats(user)

        module = RagModule()
        live: list[float] = []
        cached: list[float] = []
        search: list[float] = []
        for _ in range(runs):
            started = time.perf_counter()
            module._live_lexical_stats(user, terms)
            live.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            read_lexical_stats(user, terms)
            cached.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            module._lexical_search(user, "закон ремонт тишина", 20)
            search.append((time.perf_counter() - started) * 1000)

        for label, values in (
            ("live count (what the query did before)", live),
            ("cached statistics (two primary-key reads)", cached),
            ("_lexical_search end to end (as shipped)", search),
        ):
            p50, p95 = _ms(values)
            print(f"chunks: {chunks}")
            print(f"  {label:<42} p50 {p50:7.2f} ms  p95 {p95:7.2f} ms")
    finally:
        _cleanup(user)


def cmd_store(chunks: int) -> None:
    from app.database import get_db
    from app.db import store_rag_chunks
    from app.lemmatizer import lemmas as lemmatize

    user = _user()
    texts = [" ".join(random.Random(i).choices(WORDS, k=70)) for i in range(chunks)]
    started = time.perf_counter()
    rows = [(user, "doc-1", i, "bench.txt", text, lemmatize(text)) for i, text in enumerate(texts)]
    lemmatized = time.perf_counter() - started

    try:
        _cleanup(user)
        one_by_one = time.perf_counter()
        with get_db() as conn:
            cursor = conn.cursor()
            for row in rows:
                cursor.execute(
                    "INSERT INTO rag_chunks (user_id, doc_id, chunk_index, filename, text, lemmas)"
                    " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    row,
                )
        one_by_one = time.perf_counter() - one_by_one

        _cleanup(user)
        shipped = time.perf_counter()
        store_rag_chunks(user, "doc-1", "bench.txt", texts)
        shipped = time.perf_counter() - shipped

        print(f"chunks: {chunks}")
        print(f"  lemmatization only:            {lemmatized * 1000:7.1f} ms")
        print(f"  one INSERT per chunk:          {one_by_one * 1000:7.1f} ms")
        print(f"  store_rag_chunks (as shipped): {shipped * 1000:7.1f} ms  "
              f"({shipped / max(1, chunks) * 1000:.2f} ms/chunk)")
    finally:
        _cleanup(user)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stats = sub.add_parser("stats", help="BM25 statistics: live count, cache and the shipped query")
    stats.add_argument("--chunks", type=int, default=1000)
    stats.add_argument("--runs", type=int, default=20)
    store = sub.add_parser("store", help="chunk mirroring: per-row insert against the shipped writer")
    store.add_argument("--chunks", type=int, default=300)
    args = parser.parse_args(argv)

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is required: this benchmark writes its own rows", file=sys.stderr)
        return 2

    if args.command == "stats":
        cmd_stats(args.chunks, args.runs)
    else:
        cmd_store(args.chunks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
