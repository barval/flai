# Documents and RAG

## Upload limits

| What | Limit | Variable |
|------|-------|----------|
| One document | 25 MB | `MAX_DOCUMENT_SIZE_MB` |
| Documents per user | 250 | `MAX_DOCUMENTS_PER_USER` |
| Documents storage per user | 250 MB | `MAX_DOCUMENTS_STORAGE_MB` |
| Folders per user | 250 (same as the documents limit) | `MAX_DOCUMENTS_PER_USER` |
| Scanned-PDF OCR budget | 30 min per document | `OCR_TIME_BUDGET_S` (0 = unlimited) |

When the OCR budget expires, the pages recognized so far are indexed and the user is notified how far the recognition went.

## 1. Configure RAG in Admin Panel

After starting the services, log in as admin and go to **Admin Panel → Models** tab. Scroll down to the **Chunks** section. Here you can fine-tune RAG behavior:

- **Chunk Size (characters):** How documents are split into pieces for indexing.
- **Chunk Overlap (characters):** Number of overlapping characters between consecutive chunks.
- **Chunk Strategy:** `fixed` (by character count) or `recursive` (by headings/paragraphs).
- **Number of chunks (top_k):** Maximum number of chunks to retrieve from Qdrant per query.
- **Threshold (documents):** Minimum similarity score for general document queries.
- **Threshold (reasoning):** Minimum similarity score when RAG is triggered from a reasoning request.

Click **Save** to apply changes. If chunking parameters (size or strategy) are modified, a background reindex of all documents is triggered automatically.

> **Note:** Environment variables like `RAG_CHUNK_SIZE` in `.env` are only used as initial defaults before the first configuration save. The primary configuration is stored in the database.

## 2. Enable in Docker Compose
```bash
docker compose -f docker-compose.gpu.yml --profile with-rag up -d
```

## 3. Upload Documents
1. Log in to web interface
2. Click **Documents** tab in sidebar
3. Click ➕ to upload PDF, DOC, DOCX, TXT, ODT, RTF, CSV, JSON, or EPUB files
4. Wait for indexing to complete (status: ✅ Indexed)

## 4. Search Your Documents in Chat

Once documents are indexed, asking about them is automatic:

1. Make sure the documents are in the **Documents** panel with status **✅ Indexed**.
2. Ask any question in the chat. When the answer needs your documents, the assistant calls the 📚 `rag_search` tool and streams **«📚 Searching documents...»** live, then works the retrieved fragments into the answer (RAG retrieval runs on the fast worker; the grounded answer is produced by the reasoning model). Retrieval is **coverage-safe**: if one of your indexed documents is missing from the semantic top matches, its best fragment is still forwarded to the reasoning model — the answer won't silently lose whole contracts that simply scored low on the query.
3. RAG is **per-user and per-query**: the search covers only the current user's documents, and the LLM router decides when a question actually needs them.

For deep, multi-step work across a document set — comparisons, totals, structured reports, "find every exception" — use the 🔬 **Deep Analysis** toggle instead (see [Deep Analysis Mode](#-deep-analysis-mode-rlm)).

---

## Reranking

Retrieval decides which passages are candidates; a cross-encoder decides their order by reading the
query and the passage together. `bge-reranker-v2-m3` (Q4_K_M, ~0.42 GB) runs as a **CPU-only**
llama-server registered by the llama-swap config, so it never takes a GPU slot and a search never waits
behind a chat request. The CPU group is declared `exclusive: false` and `persistent: true`, which keeps
the reranker and the embedding model resident together — without that, the two evict each other and
every query pays two model loads (3.5 s instead of 0.15 s).

Enable it with `RAG_RERANK_ENABLED=true`; `RAG_RERANK_CANDIDATES` (default 10) sets how many top
candidates are reordered. The endpoint returns raw logits, so scores are sigmoided and scaled to the
best candidate — otherwise an absolute relevance threshold would drop every reranked chunk.

Measured on the same sets as hybrid search: ranking improves further (nDCG@10 0.838 → 0.918 on the
mixed set, MRR 0.646 → 0.742) at ~170 ms per query; keeping the pool at 10 matters, because reranking
20 candidates pushed a block of valid entries to the end of the context and the answer regressed.

Download (optional):

```bash
curl -L -o models/bge-reranker-v2-m3-Q4_K_M.gguf \
  https://huggingface.co/gpustack/bge-reranker-v2-m3-GGUF/resolve/main/bge-reranker-v2-m3-Q4_K_M.gguf
```

## Chunking strategies

Three strategies are available in Admin Panel → Models → Chunks:

| Strategy | Behaviour |
|---|---|
| `fixed` | Character windows with overlap; cheapest, cuts through sentences |
| `recursive` | Splits by paragraphs, sentences and words; keeps sentences whole |
| `structured` | Splits on logical entries: a resume entry ("Ноябрь 2023 — Сбер / Нижний Новгород / …") or a dated block stays in one chunk, its closing date and city lines included; a block larger than the chunk size falls back to the recursive splitter |

`structured` exists for counting questions ("how many jobs in this city"): when two
entries share a chunk the model has to count inside a fragment and usually gets it
wrong. The changelog entry for hybrid retrieval records the measurement — retrieval
was no longer the bottleneck, chunking was.

Changing the strategy re-chunks on the next index. Re-index existing documents with
`flask rag-reindex --user <login>` (or `--doc <id>`); the old points are deleted
first, so no orphan chunks survive.

## Search scope

The chat asks *which documents* a question may be answered from. The scope lives next to the input
(🔍): a folder and an upload-date range (both ends inclusive), combined with AND. With nothing set the
search is global, exactly as before.

- **Resolution.** The scope is turned into a list of document ids in PostgreSQL before the question is
  queued, so a re-queued task keeps its scope, moving a document between folders takes effect
  immediately, and no payload change or re-index is needed.
- **Isolation.** Retrieval never sees a document outside the scope. An **empty** match is a real
  answer — the search returns nothing and the model says there is no data in the selected documents
  instead of silently answering from the rest of the corpus.
- **Attachments.** A document attached to the message is always part of the context; the scope narrows
  only the rest of the search.
- **Coverage.** The file-coverage pass (the guarantee that every indexed document gets a slot) is
  limited to the same scope.
- **Period** is the upload date, not a date found inside the document.
- **API.** `POST /api/send_message` accepts `filter_folder`, `filter_from`, `filter_to` (multipart or
  JSON). The public `/v1` API does not expose the scope yet.

## Hybrid retrieval

Search is dense by default: the query is embedded and Qdrant returns the closest chunks. That misses
queries built around a rare word — "how many jobs in Moscow" ranks chat chatter about Moscow above the
resume, because a vector blurs the one term that matters.

With `RAG_HYBRID_ENABLED=true` every chunk is also mirrored into PostgreSQL (`rag_chunks`, a generated
`tsvector` with Russian stemming) and both retrievers run: Qdrant by meaning, PostgreSQL full-text by
terms. The two ranked lists are merged with **reciprocal rank fusion** (`RAG_RRF_K`, default 60), so a
chunk both finders agree on outranks a chunk only one found. Lexical terms are chosen carefully:

- proper nouns and Latin-script words are the discriminative ones (cities, companies, tool names);
- terms that appear in a large share of the corpus are dropped (`ts_rank_cd` has no IDF, so a name
  present in half the chunks would drown the city present in five);
- matching is by prefix, because the Russian stemmer maps `Саров` to `сар` but `Сарове` to `саров`;
- a chunk from a document whose **title** matches the query gets a bonus — the resume is named after
  the person, a chat export is not.

Existing documents are mirrored with `flask rag-backfill-chunks --user <login>`; new uploads are
mirrored automatically and deleted together with their vectors. Configuration: `RAG_HYBRID_ENABLED`,
`RAG_HYBRID_CANDIDATES` (per-retriever candidate pool, default 30), `RAG_RRF_K`; all three are also in
Admin Panel → Settings. Measure the effect with `docs/RAG_EVAL.md` before enabling it everywhere.

## 5. Documents attached in chat

A document can be attached directly to a chat message (the same 📎 button, PDF/DOC/DOCX/TXT/ODT/RTF/CSV/JSON/EPUB up to `MAX_DOCUMENT_SIZE_MB`). It is saved as a full user document, indexed through the normal pipeline (text extraction; scanned PDFs go through page OCR within `OCR_TIME_BUDGET_S`, and a partial notice is saved if the budget expires), and the question is answered over it through RAG — no manual Documents-panel step needed. The indexed document appears in the Documents panel and counts toward the per-user quota; images may accompany it in the same message and provide the visual context.

Chat attachments (images, and the attached document) are stored on disk under the session's upload folder; their paths live in the message record, so every image stays visible after a page reload. Deleting a session unlinks all files referenced by its messages — including every attached image, not just the first one — and subtracts their sizes from the user's storage quota. The `flask cleanup-uploads` command collects references from the same two sources, so it never deletes files of live sessions, and after deleting orphans it rebuilds every user's `user_storage` counter from the files that remain (a counter that was never decreased outside session deletion could keep an empty account over quota).

## 6. Organize documents in folders

Single-level folders (no nesting) keep the Documents panel tidy.

- **Create** — the 📁 button above the list opens a dedicated folder-name dialog (a native `prompt()` is not used): enter the name (≤ 64 chars, duplicates rejected), confirm with **Create** or Enter, close with **Cancel**/Esc/✕; duplicate or invalid names show an inline error under the field. **Rename** — ✏️ on the folder opens the same dialog pre-filled. **Delete** — 🗑️ asks twice: a plain confirm first, then a second one stating «this will also delete N documents (SIZE) inside the folder» — the cascade removes RAG entries, files on disk and DB rows, and frees quota exactly like deleting the documents one by one.
- **Collapse/expand** — click the folder header (📂 expanded / 📁 collapsed); the state is remembered per user in `localStorage`.
- **Move** — the 📂 button on a document opens a picker (root + every folder) and works on mobile; on desktop you can also drag a document onto a folder header.
- **Upload into a folder** — when folders exist, ➕ shows a target menu (root or a folder); otherwise it uploads into the root as before.
- **Bulk actions** — every document has a checkbox; folder checkboxes follow the all/partial/none tristate. The bar appears once something is checked and reads «Selected N · SIZE» with three buttons: **➤ 📂** (move into a folder — hidden while the Deep Analysis toggle is on), **✖** (clear selection) and **🗑️** (delete the selected documents — confirms with «You want to delete N selected documents on SIZE?», then deletes them one by one through the same pipeline as single deletion and reloads the list).
- **One selection set for moves and Deep Analysis** — the checkboxes are the single document picker. With the 🔬 toggle on, checking a document marks it for the RLM corpus (green frame + check mark); with the toggle off the same checkboxes drive the move bar. The pick mirrors into the analysis set in both modes, so un-checking a folder also clears the green highlight of its documents; switching the toggle off clears the corpus instantly, and turning it back on re-derives it from the current checkboxes.

API: `GET /api/documents` now returns `{folders, documents}`, where each folder carries `id`, `name`, `created_at`, `count` and `size`; `POST /api/document-folders` (create), `PATCH /api/document-folders/<id>` (rename), `DELETE /api/document-folders/<id>` (cascade, returns `deleted_documents`/`deleted_size`), `PATCH /api/documents/<doc_id>/folder` (move one; `folder_id: null` moves to the root) and `POST /api/documents/move` (`{doc_ids, folder_id}`) for bulk moves. Document uploads accept an optional `folder_id` form field, validated against ownership.

## 7. Document preview

Click a document card in the Documents tab to open it in the main window (the chat/task window switches to a read-only preview). The header shows «Preview: <file name>» (truncated with an ellipsis when long) in the same style as the chat/task headers; the «Messages» counter is hidden and the only action is 💾 **Save**, which downloads the original file with its current name. Switching to another tab (or pressing Escape) restores the chat/task window.

- **PDF and images** are served inline (`/api/documents/<id>/preview/file`) and rendered by the browser (`<iframe>` / `<img>`). On phones (≤ 768 px) the PDF iframe is replaced by **server-rendered page images** (`pdftoppm` → JPEG, `/api/documents/<id>/preview/pdf-page/<n>`, the first `PREVIEW_PDF_MAX_PAGES` = 30 pages, with a «Showing the first N pages» note) plus an «Open PDF» link — Chrome for Android cannot display a PDF inside an iframe and shows «This content is blocked» instead.
- **Plain text** (`.txt`, `.md`, `.csv`, `.json`) is shown as-is.
- **DOCX, ODT, RTF, EPUB** are converted to text (`extract_text_from_file`) and labelled **«Extracted text»** — the original formatting is not reproduced.
- A format without a preview gets a localized «Preview is not available for this format» note. Text previews are capped at 200 000 characters with a «Preview is truncated» note.

All preview endpoints are owner-scoped and use the same `realpath` containment check as the download route; only PDF/images are served inline (never HTML).
