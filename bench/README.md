# Benchmarks

Scripts that measure one hot path of the retrieval pipeline against a real
database. They are deliberately outside `tests/`: each one writes its own
synthetic rows under a `bench_lexical_<pid>` user, removes them in a `finally`
block, and takes seconds rather than milliseconds.

They need `DATABASE_URL` and refuse to run without it — there is no mock mode,
because the point is to measure PostgreSQL.

## `python -m bench.lexical stats --chunks N [--runs 20]`

Compares the two ways BM25 gets its corpus numbers for one query:

- **live count** — `count(*)`, `avg(cardinality(lemmas))` and the per-term
  document frequency computed over the whole corpus. This is what the query
  path did before the cache existed, and it is still the fallback for a user
  with no cache row.
- **cached lookup** — the two primary-key reads of `rag_stats` and
  `rag_term_df` that the query path uses now.

The live number grows with `--chunks`; the cached one should not. If it does,
the cache is not being consulted.

## `python -m bench.lexical store --chunks N`

Times three phases of mirroring one document: lemmatization (pymorphy3, pure
Python), one `INSERT` per chunk, and one `executemany` for the whole document.
The insert comparison measures round-trip cost only; on a database in the same
container the two are close, and the difference appears when the connection
crosses a network.

## Reading the output

Every subcommand prints `p50` and `p95` in milliseconds. A change is accepted on
a number from this output, not on an argument about complexity: run the same
subcommand before and after and put both lines in the changelog.
