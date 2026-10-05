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

1. **Extraction (background, CPU-only).** After a user message is answered, a background thread runs the rule-based extractor over the message text. It splits sentences, scores them by category patterns (preferences, facts, instructions, personality) and keeps what scores high enough. No LLM is involved — this is why extraction does not take a GPU slot or count against the user quota.
2. **Dedup and merge (background queue).** New facts are compared against existing ones. Semantic similarity above `SLM_SIMILARITY_THRESHOLD` (0.85) means "same fact" and the pair is merged rather than stored twice. Near-duplicates are also folded in by edit distance, and fragment facts are merged into a stronger existing statement.
3. **Temporal decay.** Facts older than `SLM_TEMPORAL_DECAY_DAYS` (90) with confidence below `SLM_MIN_CONFIDENCE_FOR_DECAY` (0.5) are auto-archived.
4. **Recall.** When the reasoning model builds its context, up to `SLM_RECALL_LIMIT` facts are fetched first and measured for real token cost before anything else is added. The router additionally sees up to `ROUTER_SLM_FACTS` (2) facts when deciding what a message is about.

An explicit request to remember something — «запомни, что я не ем грибы» — is parsed by the extractor's explicit-remember path rather than being left to the scoring heuristics.

## Configuration

```bash
# Recall
SLM_URL=http://flai-slm:8766
SLM_RECALL_LIMIT=7
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

Import past conversations into memory:

```bash
docker exec flai-web flask import-history-to-slm [--force] [user_id]
```

Prune and merge memories, either for everyone or for one profile:

```bash
# All users
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"

# Single user
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({'profile':'valery'}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"
```

The SLM container is healthy when `GET http://localhost:8766/health` answers. In `docker-compose.gpu.yml` its healthcheck polls port **8766** while the container is configured with `SLM_PORT=8765` — check the healthcheck output before assuming memory is broken:

```bash
docker inspect --format '{{.State.Health.Status}}' flai-slm
docker logs flai-slm --tail 50
```

## See also

- [DOCUMENTS.md](DOCUMENTS.md) — RAG over uploaded documents (different mechanism: vector search over files, not learned facts)
- [ADMINISTRATION.md](ADMINISTRATION.md) — user deletion and the GDPR erasure path
- [../CHANGELOG.md](../CHANGELOG.md) — when rule-based extraction and merge landed