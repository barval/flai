# Documents and RAG


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
