# RAG quality measurement

The harness answers one question: **did a change make document search better or worse?** It runs a
fixed question set through the real search path and reports retrieval metrics plus the latency each
query costs. Two runs on the same dataset are directly comparable.

Code: `app/rag_eval.py` (metrics and report), CLI commands `rag-eval` and `rag-eval-generate`
(`app/cli.py`). Tests: `tests/test_rag_eval.py`.

## Dataset format

JSONL, one question per line:

```json
{"question": "What is the penalty for late payment?", "relevant": ["<doc_id>", "contract.pdf"], "reference": "The penalty is 5% per day of delay"}
```

- `relevant` — document ids or file names that count as the right answer.
- `reference` — optional passage the question was generated from. When present the check becomes
  **chunk-level**: the retrieved chunk must contain that passage, not merely come from the right file.
  Without it the check is document-level, which saturates quickly on a small corpus.

## Commands

```bash
# Build a question set from the user's indexed chunks (one model call per question)
flask rag-eval-generate --user valery --count 20 --out data/rag_eval/questions.jsonl

# Measure
flask rag-eval --user valery --dataset data/rag_eval/questions.jsonl \
               --top-k 10 --label "dense baseline" --out data/rag_eval/baseline.json
```

Options: `--coverage` enables the file-coverage pass, `--lang` and `--model` control generation.
The report also records the settings snapshot (`RAG_TOP_K`, chunk size, overlap, strategy, context
share), so a report is self-describing.

### Answer quality (end-to-end)

Retrieval metrics cannot catch a model that finds the right chunks and still counts wrong, which is
the usual failure for "how many jobs in this city" questions. Add an `"answer"` field to a dataset
item and run the same command with `--answers`:

```json
{"question": "How many jobs did he have in Moscow?", "relevant": ["<doc_id>"], "answer": "0"}
```

The harness retrieves, builds the context exactly like a chat request, asks the model (it is told to
answer `НЕТ ДАННЫХ` when the fragments do not contain the answer) and compares the result: numbers
compare by number, free text by normalized containment, and an expected `0` also accepts an explicit
"no data" answer. The report lists every mismatch with the expected and actual answer.

## Metrics

| Metric | Meaning |
|---|---|
| `hit@k` | Share of questions with at least one relevant chunk in the top k |
| `recall@k` | Share of the relevant documents found in the top k |
| `MRR` | Mean reciprocal rank of the first relevant result (1.0 = always first) |
| `nDCG@k` | Ranking quality, counting each document once |
| `latency p50/p95` | Search time per query (embedding + Qdrant), milliseconds |
| `context chars` | Characters the query hands to the model |

Latency matters as much as quality: hybrid search and a reranker buy accuracy with time, and the
numbers above are what decides whether the trade is worth it.

## Baseline

Two datasets matter, and they disagree — that disagreement is the finding.

**Synthetic set** (19 questions generated from the chunks, chunk-level ground truth, `top_k=10`):
these queries are already close to their source chunk, so dense retrieval is strong and the lexical
half mostly adds noise.

| Run | hit@10 | recall@10 | MRR | nDCG@10 | p50 | p95 |
|---|---|---|---|---|---|---|
| dense | 0.842 | 0.842 | 0.781 | 0.796 | 14 ms | 44 ms |
| hybrid (RRF) | 0.842 | 0.842 | 0.671 | 0.715 | 31 ms | 59 ms |

**Counting questions** (three real "how many jobs in <city>" questions with known answers) are where
dense retrieval fails outright: the chat export outranks the resume. Retrieval on the mixed 22-item
set goes 0.773 → 0.864 hit@10, and end-to-end answers with the production `top_k=20`:

| Config | answers correct |
|---|---|
| dense | 0/3 |
| hybrid | 3/3 |

So hybrid is worth its 17 ms on entity-style questions and costs a little ranking quality on easy
ones; it stays opt-in (`RAG_HYBRID_ENABLED`), and that trade-off is the reason.

Every candidate change (reranker, metadata filters, chunking) is measured on both datasets.

## Notes

- A set of 5 questions proves nothing: generation is cheap, use 20–30.
- Questions are paraphrased on purpose (the prompt forbids copying the passage wording); otherwise
  dense search scores 1.0 trivially and the harness cannot tell approaches apart.
- Rebuild the dataset only when the corpus changes materially; otherwise runs stop being comparable.
- Datasets and reports contain real documents and personal data: keep them under `data/rag_eval/`, which is git-ignored. Nothing from there is committed or pushed.
