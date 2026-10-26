# Long-Term Memory (SLM)

FLAI remembers facts across sessions. Every user has a persistent memory profile; facts extracted from conversation are recalled into the reasoning context of later requests.

The engine is **SuperLocalMemory (SLM)** — an optional sidecar container. The extraction and merge logic that decides *what* is worth remembering lives in FLAI itself (`app/slm_rules.py`) and runs on CPU, with no LLM call.

## Enabling

Memory is optional and off by default. Start the profile:

```bash
docker compose -f docker-compose.gpu.yml --profile with-slm up -d
```

Then set the variables in `.env`:

```bash
SLM_URL=http://flai-slm:8766
SLM_RECALL_LIMIT=7
```

Restart the web container so it picks up `SLM_URL`:

```bash
docker compose -f docker-compose.gpu.yml restart web
```

Without `SLM_URL` the instance is fully functional — facts are simply never extracted, and no memory facts are injected into prompts.

## How a fact is remembered

1. **Candidate check (CPU, no model).** After an answer the exchange is queued only
   when the message looks personal (a first-person marker or an explicit "remember").
   This is a cost filter, not a router: a missed fact is cheaper than a model call on
   every message about the weather.
2. **Extraction (background queue, the resident multimodal model).** A strict JSON
   schema asks only for durable facts about the user — profile, preferences,
   projects, standing instructions. Questions, task text, the assistant's answer,
   one-off numbers and third-party facts are rejected by the prompt *and* by the
   parser (`app/memory_facts.py`), and low-confidence entries never reach the store.
   The rule-based extractor this replaced fired on 0 of 30 real messages.
3. **Dedup and merge.** A new fact is compared against the store (`0.85` similarity)
   before it is written; near-duplicates are folded in by edit distance, and
   fragment facts are merged into a stronger existing statement.
4. **Recall.** The profile block (the newest `kind=profile` facts) is injected on
   every request regardless of the query wording; other facts only above
   `SLM_MIN_RECALL_SCORE` (0.5), so a recalled sentence that did not score costs
   neither tokens nor attention. Up to `SLM_RECALL_LIMIT` facts in total.

A wrong fact is worse than a missing one: measured before the change, the store
held image-generation prompts and arithmetic tasks, and recall returned them at
0.63 for "do I like apples?".

**Hosts without a GPU.** The extraction call is one short generation on the
model chat is already using; on a CPU-only host that is ~30 s of the same model,
so `SLM_AUTO_EXTRACT=auto` (default) runs it only where a GPU keeps the model
resident. `on` forces it, `off` disables it; the explicit "remember this" path
works either way.

**Profile consolidation.** Individual facts accumulate duplicates and
contradictions ("lives in Tver" next to an older "lives in Sarov"). The profile
block is the one place where that can be resolved once instead of on every
request:

```bash
docker exec flai-web flask memory-profile --user <login>          # shows the block
docker exec flai-web flask memory-profile --user <login> --apply  # stores it
```

The block is stored as a `kind=profile` fact and, being the newest, leads every
recall. The individual facts stay in the store.

**Measuring memory.** RAG has had `flask rag-eval` since v12.5; memory now has
the same harness:

```bash
docker exec flai-web flask memory-eval --user <login> --dataset /app/data/q.jsonl
docker exec flai-web flask memory-eval --user <login> --dataset /app/data/q.jsonl --no-profile
```

The dataset is JSONL with `{"question": "…", "expected": ["substring", …]}`. The
report gives hit@k, MRR, the number of injected facts (the context cost) and
recall latency. `--no-profile` scores recall alone: the always-injected profile
otherwise takes rank 1 and distorts MRR. Measured on a five-question set over a
live profile: hit@5 0.8, MRR 0.7, 1.6 facts per question, p50 4.5 ms — the one
miss was a Russian question against an English fact, which is the lexical
recall's real limit, not a threshold problem.

## Configuration

```bash
# Recall
SLM_URL=http://flai-slm:8766
SLM_RECALL_LIMIT=7
SLM_MIN_RECALL_SCORE=0.5
ROUTER_SLM_FACTS=2

# Rule-based extraction and merge
SLM_SIMILARITY_THRESHOLD=0.85
SLM_TEMPORAL_DECAY_DAYS=90
SLM_MIN_CONFIDENCE_FOR_DECAY=0.5

# Background merge job
MERGE_MAX_FACTS=100
MERGE_CONTEXT_SIZE=4096
MERGE_FACT_MAX_CHARS=120
MERGE_MAX_FIT_FACTS=62
```

`MERGE_MAX_FIT_FACTS` is auto-calculated from `MERGE_CONTEXT_SIZE` — set it only if you need to override.

## Memory is per user

Each FLAI account owns one SLM profile, keyed by the user's login. Deleting a user erases the corresponding SLM profile in the same operation (`userdb.delete_user()` calls the wrapper's `/delete-profile` route and warns if it fails), along with the on-disk profile directory under `data/slm/<login>/`. This is part of the GDPR erasure path — see [ADMINISTRATION.md](ADMINISTRATION.md).

## Maintenance

Inspect what the store holds and what would be injected:

```bash
docker exec flai-web flask memory-report --user <login> --query "what do I avoid?"
```

Remove facts written by one source (the history import stored every user message
verbatim):

```bash
docker exec flai-web flask memory-purge --user <login> --source auto_import        # dry run
docker exec flai-web flask memory-purge --user <login> --source auto_import --yes  # delete
```

Import past conversations into memory:

```bash
docker exec flai-web flask import-history-to-slm [--force] [user_id]
```

Prune and merge memories, either for everyone or for one profile:

```bash
# All users
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"

# Single user
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({'profile':'<login>'}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"
```

The SLM container runs two processes: the SuperLocalMemory daemon on **8765** and the profile wrapper
that FLAI talks to on **8766**. The healthcheck polls the wrapper, which is the only port the web
container uses. Check it before assuming memory is broken:

```bash
docker inspect --format '{{.State.Health.Status}}' flai-slm
docker logs flai-slm --tail 50
```

## See also

- [DOCUMENTS.md](DOCUMENTS.md) — RAG over uploaded documents (different mechanism: vector search over files, not learned facts)
- [ADMINISTRATION.md](ADMINISTRATION.md) — user deletion and the GDPR erasure path
- [../CHANGELOG.md](../CHANGELOG.md) — when rule-based extraction and merge landed