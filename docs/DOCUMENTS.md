# Documents and RAG

## Upload limits

| What | Limit | Variable |
|------|-------|----------|
| One document | 25 MB | `MAX_DOCUMENT_SIZE_MB` |
| Documents per user | 50 | `MAX_DOCUMENTS_PER_USER` |
| Documents storage per user | 250 MB | `MAX_DOCUMENTS_STORAGE_MB` |
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

## 5. Documents attached in chat

A document can be attached directly to a chat message (the same 📎 button, PDF/DOC/DOCX/TXT/ODT/RTF/CSV/JSON/EPUB up to `MAX_DOCUMENT_SIZE_MB`). It is saved as a full user document, indexed through the normal pipeline (text extraction; scanned PDFs go through page OCR within `OCR_TIME_BUDGET_S`, and a partial notice is saved if the budget expires), and the question is answered over it through RAG — no manual Documents-panel step needed. The indexed document appears in the Documents panel and counts toward the per-user quota; images may accompany it in the same message and provide the visual context.

Chat attachments (images, and the attached document) are stored on disk under the session's upload folder; their paths live in the message record, so every image stays visible after a page reload. Deleting a session unlinks all files referenced by its messages — including every attached image, not just the first one — and subtracts their sizes from the user's storage quota. The `flask cleanup-uploads` command collects references from the same two sources, so it never deletes files of live sessions.

## 6. Organize documents in folders

Single-level folders (no nesting) keep the Documents panel tidy.

- **Create** — the 📁 button above the list, then the folder name (≤ 64 chars, duplicates rejected). **Rename** — ✏️ on the folder (the prompt is pre-filled). **Delete** — 🗑️ asks twice: a plain confirm first, then a second one stating «this will also delete N documents (SIZE) inside the folder» — the cascade removes RAG entries, files on disk and DB rows, and frees quota exactly like deleting the documents one by one.
- **Collapse/expand** — click the folder header (📂 expanded / 📁 collapsed); the state is remembered per user in `localStorage`.
- **Move** — the 📂 button on a document opens a picker (root + every folder) and works on mobile; on desktop you can also drag a document onto a folder header.
- **Upload into a folder** — when folders exist, ➕ shows a target menu (root or a folder); otherwise it uploads into the root as before.
- **Bulk actions** — every document has a checkbox; folder checkboxes follow the all/partial/none tristate. The «Selected N · Move to… · Clear selection» bar appears once something is checked; «Move to…» opens the same picker.

API: `GET /api/documents` now returns `{folders, documents}`, where each folder carries `id`, `name`, `created_at`, `count` and `size`; `POST /api/document-folders` (create), `PATCH /api/document-folders/<id>` (rename), `DELETE /api/document-folders/<id>` (cascade, returns `deleted_documents`/`deleted_size`), `PATCH /api/documents/<doc_id>/folder` (move one; `folder_id: null` moves to the root) and `POST /api/documents/move` (`{doc_ids, folder_id}`) for bulk moves. Document uploads accept an optional `folder_id` form field, validated against ownership.
