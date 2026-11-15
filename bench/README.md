# Benchmarks

Scripts that measure one hot path of the retrieval pipeline against a real
database. They are deliberately outside `tests/`: each one writes its own
synthetic rows under a `bench_lexical_<pid>` user, removes them in a `finally`
block, and takes seconds rather than milliseconds.

They need `DATABASE_URL` and refuse to run without it — there is no mock mode,
because the point is to measure PostgreSQL.

## `python -m bench.lexical stats --chunks N [--runs 20]`

Prints three numbers for one query against a corpus of N chunks:

- **live count** — `count(*)`, `avg(cardinality(lemmas))` and the per-term
  document frequency computed over the whole corpus. This is what the query
  path did before the cache existed, and it is still the fallback for a user
  with no cache row.
- **cached statistics** — the two primary-key reads of `rag_stats` and
  `rag_term_df` that the query path uses now.
- **`_lexical_search` end to end** — the shipped method, including the
  candidate fetch and the scoring loop.

The live number grows with `--chunks`; the cached one should not.

## `python -m bench.lexical store --chunks N`

Times three phases of mirroring one document: lemmatization alone (pymorphy3,
pure Python), one `INSERT` per chunk, and `store_rag_chunks` as shipped — the
one that also deletes the previous mirror, writes in one `execute_values`
statement per 500-row page, and rebuilds the statistics. The last line is the
one a user waits for on upload; the per-chunk figure next to it is what scales.

## Reading the output

`stats` prints `p50`/`p95` in milliseconds; `store` prints total milliseconds
and milliseconds per chunk. A change is accepted on a number from this output,
not on an argument about complexity: run the same subcommand before and after
and put both lines in the changelog.
