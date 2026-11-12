# Knowledge Graph

The knowledge graph is a second way to look at the document library: instead of questions and
answers it shows *who and what the documents are about*, and how those things connect. It is built
from the indexed chunks, stored in PostgreSQL and drawn as a 3D cloud (2D where the browser provides
no WebGL).

**It is off as a retrieval source by default, and that is a measured decision, not an unfinished
one** — see [Measurements](#measurements). Its primary purpose today is to make relations in a corpus
visible and explorable.

## What it is for

- **Seeing structure.** Open the whole cloud, or one document's neighbourhood, and see which
  organisations, people, projects, places and terms the corpus is built around.
- **Following relations.** Click a node to see its neighbours and the documents behind each relation;
  right-click two nodes to draw the shortest path between them.
- **Finding the documents behind a relation.** Every relation names its source document, so a
  curiosity about «who supplies what to whom» ends in the file that says so.
- **Auditing a corpus.** A large share of isolated entities means the documents name things without
  relating them — worth knowing before trusting the corpus for analysis.

## How it is built

1. **Documents are indexed first.** The graph reads `rag_chunks`, so a document must be indexed by the
   ordinary upload path.
2. **Each chunk is extracted on the idle background queue** — one chunk per task, on the resident
   multimodal model, with the neighbouring text passed as context. Any chat or API request pre-empts
   the extraction: the task aborts mid-generation and re-queues itself, so nothing half-written
   survives. A build therefore progresses only while the instance is idle.
3. **Entities and relations are written** into `kg_nodes`, `kg_occurrences` and `kg_edges`.
4. **The document layer** is computed from chunk centroids (`kg_doc_vectors`, `kg_doc_edges`): two
   documents are linked when their mean vectors are close. This layer needs no model call.

Extraction runs automatically for new documents when `GRAPH_AUTO_EXTRACT=auto` (a GPU host) and stays
off on a CPU-only host, where a backfill would take days.

## Entities and relations

- **Entity kinds** are closed: `person`, `organization`, `project`, `document`, `event`, `place`,
  `term`. A model answer with any other kind is rejected outright — a half-parsed extraction would put
  invented facts into a graph that later answers questions with them.
- **Entity keys are lemma sets**, sorted, so word order carries nothing: a name written given-name
  first and the same name written surname-first are one node, and a name that extends a shorter one
  word by word (a patronymic, a full legal name) is the same entity. Leading and trailing legal forms
  are dropped, so a company with and without its legal form is one node.
- **Predicates are a closed list** of twelve relations in each language (`supplies`, `belongs to`,
  `works for`, `leads`, `signed`, `rents`, `maintains`, `performs`, `forbids`, `allows`, `located in`,
  `related to`). An open list was measured to produce ad-hoc phrases that never merge across documents
  and cannot gain weight; a free-form predicate is now folded onto the list by stem overlap
  («delivered equipment» and «is the supplier of» both become `supplies`).
- **Evidence is checked against the text.** A relation survives only when its quoted evidence is made
  of words the chunk (or its immediate neighbourhood) actually contains, and an endpoint the model
  named inside a relation is created only when its words are in that window. An invented entity would
  merge with a same-named node elsewhere and seed unrelated questions.
- **Language is per chunk.** The prompt language follows the chunk's own script, the predicate list is
  offered in both languages and matched as a union, and English keys are folded with a suffix rule set
  (the stack has no English morphological analyser) — so one corpus may mix languages.

## Retrieval use, when enabled

`GRAPH_RAG_ENABLED=true` adds a graph stage to the end of `RagModule.search()`:

1. Seeds are the question's lemmas (weighted by how rare each lemma is) plus the entities mentioned in
   the chunks the dense half already retrieved.
2. The traversal walks `GRAPH_HOPS` steps inside the caller's scopes — own documents plus the
   documents other accounts shared with the caller.
3. The source chunks of the strongest relations join the candidate pool, marked `origin="graph"`.
   They borrow the best fused score, because a structural relation has no cosine to compare, and they
   are bounded by `GRAPH_SHARE` — a ceiling on what the graph may add, not a deduction from the dense
   half.
4. The strongest relations, and the shortest path when two entities were seeded, reach the prompt as a
   separate block capped by `GRAPH_SECTION_TOKENS`.

Any failure in the stage — a disabled flag, an empty graph, an exception — leaves the retrieval exactly
as the dense and lexical halves produced it.

## Building and inspecting

```bash
flask graph-build --user <login>              # queue documents that still have chunks to extract
flask graph-build --user <login> --doc <id>   # one document
flask graph-build --user <login> --rebuild    # throw the stored graph away and extract again
flask graph-status --user <login>             # per-document progress, node and edge counts
flask graph-stats --user <login>              # extraction quality: density, isolated share, predicates
flask graph-merge-entities --user <login>     # merge nodes that name one entity in different words
```

`graph-stats` is the quality gauge that needs no labelled data:

| Proxy | What it says |
|---|---|
| entities per chunk | how much the model actually extracts |
| relations proposed / kept | how much survives the evidence check |
| edges per node (density) | whether the graph has paths or only dots |
| isolated share | how many entities are mentioned without any relation |
| top predicates | whether the vocabulary stays closed |

A build costs roughly 4–5 seconds per chunk on the idle queue (a 840-chunk corpus took about an hour),
and it is interrupted by every conversation.

`graph-merge-entities` is the repair pass for duplicates that a build can produce when the model writes
one entity several ways: it merges identical lemma sets first, then a name that extends a shorter one
word by word, moving occurrences and edges onto the longer name.

## View

The view is a mode of the chat main window, entered from 🕸️ on a document card (that document's
neighbourhood) or from the panel header (the whole cloud).

- **Layers.** Entities and documents are two layers of one cloud. The single layer button names the
  action: while entities are shown it offers documents, and the other way round.
- **2D and 3D.** 3D is the default; the switch names the action too. A browser without WebGL falls back
  to 2D once and says why on the status line — a silent fallback looked like a broken button.
- **Colours.** Entity nodes are coloured by kind. In the document layer, the caller's own documents keep
  the neutral colour and documents shared by other accounts are marked, with the owner named on the
  node card.
- **Navigation.** Sidebar tabs are navigation: opening Documents, Tasks or Sessions closes the graph and
  shows that section.
- **Controls.** Click a node to inspect it, right-click two nodes for the path between them, search to
  fly to a node, and the cloud is fitted to the view once when it opens.

The renderer (`3d-force-graph`, MIT, with three.js) is vendored and loaded only when the view opens; no
CDN is used.

## Settings

| Key | Default | Meaning |
|---|---|---|
| `GRAPH_RAG_ENABLED` | `false` | Use the graph in retrieval |
| `GRAPH_AUTO_EXTRACT` | `auto` | `auto` (GPU host only) / `on` / `off` |
| `GRAPH_EXTRACT_MODEL` | `multimodal` | Model that performs extraction |
| `GRAPH_HOPS` | `2` | Traversal depth from a seed |
| `GRAPH_MAX_SEEDS` | `8` | Seeds per question |
| `GRAPH_MAX_EDGES` | `12` | Relations kept per question |
| `GRAPH_SHARE` | `0.15` | Ceiling on graph fragments as a share of the evidence budget (`0` = none) |
| `GRAPH_SECTION_TOKENS` | `600` | Token cap for the relation block in the prompt |
| `GRAPH_MAX_NODES` | `1000` | Nodes drawn by the view |
| `GRAPH_DOC_SIMILARITY` | `0.75` | Cosine threshold for a document-layer link |
| `GRAPH_DOC_NEIGHBOURS` | `8` | Neighbours per document in the document layer |

All eleven keys are in `app/config.py` and in the admin settings panel.

## Measurements

Taken on the released code, with the graph stage actually running (an earlier round of numbers was
worthless: `networkx` was missing from the image, the stage died at its first import and the retrieval
silently used dense search alone).

**Extraction quality, a real 96-document corpus (840 chunks):**

```
entities 3 977 (4.73 per chunk)   relations 1 039 proposed / 1 025 kept (99 %)
nodes 1 440    edges 914    density 0.63 edges per node    isolated 43 %
```

Before the extraction rewrite the same corpus gave 0.19 edges per node and 77 % isolated entities.
A 36-document control corpus reaches 1.50 entities per chunk, 31 of 31 relations kept, 0.46 edges per
node and 10 % isolated; a 12-document English corpus reaches 2.08 entities per chunk, 14 of 14
relations kept, 0.72 edges per node and 6 % isolated.

**Retrieval, 30 questions over the real corpus (identical question set, one run each):**

| | MRR | nDCG@10 | hit@10 | context | p50 |
|---|---|---|---|---|---|
| graph off | 0.770 | 0.848 | 0.967 | 31 956 chars | 187 ms |
| graph on | 0.770 | 0.848 | 0.967 | 35 528 chars | 252 ms |

The graph genuinely adds evidence (+11 % context) and costs about 65 ms; the ranking does not move,
because its fragments are additional material at the end of the candidate pool rather than better
candidates.

**Answer quality, the same 30 questions, graded by a model judge (2 = complete and correct):**

| | mean score | fully right |
|---|---|---|
| graph off | 0.400 | 30 % |
| graph on | 0.367 | 23 % |

Paired per question: 2 better, 4 worse, 24 unchanged. On a corpus built so that the bridging document
shares no wording with the question, dense retrieval already answered 5 of 5 correctly, so the graph
had nothing to add.

**Conclusion.** The graph does not improve answers on the corpora measured, so `GRAPH_RAG_ENABLED`
stays `false`. It remains a first-class way to *read* a corpus: relations, paths and source documents
that dense search cannot show.

## Limitations

- **Sparse corpora stay sparse.** A corpus of unrelated notes produces few relations no matter how good
  the extractor is; the isolated share is the honest number to watch.
- **The predicate list is a business vocabulary.** Medicine, law or engineering texts produce relations
  that fall outside it; those are stored as written and do not merge with anything.
- **Synonyms are not merged.** Two wordings of one relation («annual leave» and «vacation days») stay
  distinct, in both languages.
- **Abbreviations are not expanded.** A company abbreviated differently from its full name is two nodes
  unless a legal form is the only difference.
- **A build takes time and yields to chat.** Roughly 4–5 seconds per chunk, only while idle.

## Tests

`tests/test_graph_store.py`, `test_graph_extract.py`, `test_graph_prompt.py`, `test_graph_queue.py`,
`test_graph_cli.py`, `test_graph_rag.py`, `test_graph_docs.py`, `test_graph_api.py`, `test_graph_ui.py`,
`test_graph_settings.py`, `test_graph_search_integration.py`.
