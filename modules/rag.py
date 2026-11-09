# modules/rag.py
import logging
import math
import time
import uuid
from collections import Counter
from collections.abc import Callable

from flask import current_app
from flask_babel import force_locale
from flask_babel import gettext as _
from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.db import (
    INDEX_STATUS_INDEXED,
    find_indexed_document_by_blob,
    get_document,
    get_session_text_history,
    get_shared_document_ids,
    get_user_documents,
)
from app.lemmatizer import lemmas as lemmatize
from app.llamacpp_client import LlamaCppClient
from app.model_config import get_model_config
from app.utils import (
    build_context_prompt,
    chunk_text,
    chunk_text_recursive,
    chunk_text_structured,
    estimate_tokens,
    extract_text_from_file,
    format_chunk_entry,
    format_prompt,
    get_current_time_in_timezone,
)
from modules.base import get_style_instruction, response_language_name

BM25_K1 = 1.5
BM25_B = 0.75


def bm25_score(
    chunk_lemmas: list[str],
    terms: list[str],
    document_frequency: dict[str, int],
    n_chunks: int,
    average_length: float,
) -> float:
    """BM25 of one chunk against the query terms (k1=1.5, b=0.75)."""
    if not chunk_lemmas or not terms or n_chunks <= 0 or average_length <= 0:
        return 0.0
    counts = Counter(chunk_lemmas)
    length = len(chunk_lemmas)
    score = 0.0
    for term in terms:
        frequency = counts.get(term, 0)
        if not frequency:
            continue
        df = document_frequency.get(term, 0)
        idf = math.log((n_chunks - df + 0.5) / (df + 0.5) + 1.0)
        score += idf * frequency * (BM25_K1 + 1) / (
            frequency + BM25_K1 * (1 - BM25_B + BM25_B * length / average_length)
        )
    return score


def rrf_fuse(
    ranked_lists: list[list[dict]],
    key,
    k: int = 60,
    weights: list[float] | None = None,
) -> list[tuple[dict, float]]:
    """Reciprocal rank fusion: merge several ranked lists into one ordering.

    ``key(item)`` identifies the same chunk across lists. The fused score is the
    sum of ``1 / (k + rank)`` over the lists that returned the chunk, so a chunk
    both retrievers agree on outranks a chunk only one of them found.
    """
    scores: dict = {}
    payload: dict = {}
    for index, items in enumerate(ranked_lists):
        weight = (weights[index] if weights and index < len(weights) else 1.0) or 0.0
        for rank, item in enumerate(items, start=1):
            ident = key(item)
            scores[ident] = scores.get(ident, 0.0) + weight / (k + rank)
            payload.setdefault(ident, item)
    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    return [(payload[ident], score) for ident, score in ordered]


# How much deeper than the coverage allowance the query pool is fetched.
COVERAGE_POOL_FACTOR = 4


def apply_coverage(
    relevance: list[tuple[dict, float]],
    extras: list[tuple[dict, float]],
    coverage_max_files: int,
) -> tuple[list[tuple[dict, float]], int]:
    """Append one chunk per document the relevance half did not represent.

    ``relevance`` is the top-k as ``(chunk, score)`` pairs; ``extras`` is the
    allowance fetched with the same query. A document already represented is
    skipped, the first chunk of each of the others is appended (marked
    ``coverage``) until the allowance is spent. Returns the new list and how many
    documents were added.
    """
    covered = {str(chunk.get("doc_id")) for chunk, _score in relevance if chunk.get("doc_id")}
    kept = list(relevance)
    added = 0
    for chunk, score in extras:
        if added >= coverage_max_files:
            break
        doc_id = str(chunk.get("doc_id") or "")
        if not doc_id or doc_id in covered:
            continue
        kept.append(({**chunk, "coverage": True}, score))
        covered.add(doc_id)
        added += 1
    return kept, added

class RagModule:
    """Module for Retrieval-Augmented Generation using Qdrant and llama.cpp embeddings."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.qdrant_client = None
        self.last_coverage: dict = {}
        self.llamacpp = LlamaCppClient(app)
        self.available = False
        self.collection_name_prefix = "user_"
        self.chunk_size = 500
        self.chunk_overlap = 50
        self.chunk_strategy = "fixed"
        self.top_k = 20
        self.hybrid_enabled = False
        self.hybrid_candidates = 30
        self.rrf_k = 60
        self.rerank_enabled = False
        self.rerank_candidates = 20
        self.rerank_timeout = 10
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize module with Flask app configuration."""
        qdrant_url = app.config.get("QDRANT_URL")
        qdrant_api_key = app.config.get("QDRANT_API_KEY")

        # Try to load from DB first, fallback to config
        from app.model_config import get_model_config

        chunks_config = get_model_config("chunks")

        if chunks_config:
            self.chunk_size = chunks_config.get("chunk_size", 500)
            self.chunk_overlap = chunks_config.get("chunk_overlap", 50)
            self.chunk_strategy = chunks_config.get("chunk_strategy", "fixed")
            self.top_k = chunks_config.get("top_k", 80)
        else:
            self.chunk_size = app.config.get("RAG_CHUNK_SIZE", 500)
            self.chunk_overlap = app.config.get("RAG_CHUNK_OVERLAP", 50)
            self.chunk_strategy = app.config.get("RAG_CHUNK_STRATEGY", "fixed")
            self.top_k = app.config.get("RAG_TOP_K", 80)

        # Hybrid retrieval is an app-level switch, not part of the chunks config:
        # it must apply whether or not the chunk settings live in the database.
        self.hybrid_enabled = bool(app.config.get("RAG_HYBRID_ENABLED", False))
        self.hybrid_candidates = int(app.config.get("RAG_HYBRID_CANDIDATES", 30))
        self.rrf_k = int(app.config.get("RAG_RRF_K", 60))
        self.rerank_enabled = bool(app.config.get("RAG_RERANK_ENABLED", False))
        self.rerank_candidates = int(app.config.get("RAG_RERANK_CANDIDATES", 10))
        self.rerank_timeout = int(app.config.get("RAG_RERANK_TIMEOUT", 10))

        app.logger.info(f"RagModule: loaded chunk_size = {self.chunk_size}")
        app.logger.info(f"RagModule: loaded chunk_overlap = {self.chunk_overlap}")
        app.logger.info(f"RagModule: loaded chunk_strategy = {self.chunk_strategy}")
        app.logger.info(f"RagModule: loaded top_k = {self.top_k}")
        app.logger.info(f"RagModule: hybrid retrieval = {self.hybrid_enabled} (candidates={self.hybrid_candidates})")

        if not qdrant_url:
            app.logger.warning("QDRANT_URL not set, RAG module disabled")
            self.available = False
            return

        # Retry initial connection (Qdrant may still be starting)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
                self.qdrant_client.get_collections()
                self.available = True
                app.logger.info(f"RagModule initialized with Qdrant at {qdrant_url}, top_k={self.top_k}")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = 5
                    app.logger.warning(f"Qdrant not ready (attempt {attempt + 1}/{max_retries}), retry in {delay}s...")
                    time.sleep(delay)
                else:
                    self.available = False
                    app.logger.error(f"Failed to connect to Qdrant after {max_retries} attempts: {e}")

    def check_availability(self) -> bool:
        """Check if Qdrant is reachable. Reconnect if previously unavailable."""
        if self.available and self.qdrant_client:
            return True

        qdrant_url = current_app.config.get("QDRANT_URL")
        qdrant_api_key = current_app.config.get("QDRANT_API_KEY")
        if not qdrant_url:
            return False

        try:
            self.qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
            self.qdrant_client.get_collections()
            self.available = True
            self.logger.info(f"RagModule: reconnected to Qdrant at {qdrant_url}")
            return True
        except Exception as e:
            self.available = False
            self.logger.warning(f"RagModule: reconnection failed: {e}")
            return False

    def _get_collection_name(self, user_id: str) -> str:
        """Return collection name for a specific user."""
        return f"{self.collection_name_prefix}{user_id}"

    def _search_scopes(self, user_id: str, scope_ids: list[str] | None) -> list[tuple[str, list[str] | None]]:
        """Collections a search must visit: the caller's own and the sharers'.

        Each entry is ``(owner_login, doc_ids)``; ``doc_ids`` is ``None`` for the
        caller's own collection when the request is not scoped to attachments.
        """
        shared = get_shared_document_ids(user_id)
        scopes: list[tuple[str, list[str] | None]] = []
        if scope_ids is None:
            scopes.append((user_id, None))
        else:
            own = [doc_id for doc_id in scope_ids if doc_id not in shared]
            if own:
                scopes.append((user_id, own))
        by_owner: dict[str, list[str]] = {}
        for doc_id, owner in shared.items():
            if scope_ids is not None and doc_id not in scope_ids:
                continue
            by_owner.setdefault(owner, []).append(doc_id)
        scopes.extend(by_owner.items())
        return scopes

    def _ensure_collection(self, user_id: str):
        """
        Ensure that a collection exists for the user with the correct vector dimension.
        If the collection exists but has a different dimension, it is deleted and recreated.
        """
        collection_name = self._get_collection_name(user_id)
        # Get current embedding dimension
        test_emb = self._get_embedding("test")
        if test_emb is None:
            raise RuntimeError("Cannot get embedding to determine vector size")
        current_dim = len(test_emb)
        try:
            # Check if collection exists and get its dimension
            info = self.qdrant_client.get_collection(collection_name)
            existing_dim = info.config.params.vectors.size
            if existing_dim == current_dim:
                # All good, nothing to do
                self.logger.debug(f"Collection {collection_name} already exists with correct dimension {current_dim}")
                return
            else:
                # Dimension mismatch: delete and recreate
                self.logger.warning(
                    f"Dimension mismatch for {collection_name}: "
                    f"collection has {existing_dim}, model gives {current_dim}. Recreating."
                )
                self.qdrant_client.delete_collection(collection_name)
        except Exception as e:
            # Collection does not exist or other error – we will create it
            if "Not found" not in str(e) and "doesn't exist" not in str(e):
                self.logger.warning(f"Unexpected error checking collection {collection_name}: {e}")
            # Create the collection with current dimension
            self.qdrant_client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(size=current_dim, distance=models.Distance.COSINE),
            )
            self.logger.info(f"Created collection {collection_name} with vector size {current_dim}")

    def index_document(self, user_id: str, doc_id: str, file_path: str) -> tuple[bool, str]:
        """
        Extract text from document, chunk it, generate embeddings and store in Qdrant.
        Returns (success, message).
        """
        if not self.check_availability():
            return False, "RAG service unavailable"
        self.logger.info(f"index_document: starting for doc_id={doc_id}, file_path={file_path}")

        # Get document metadata from DB (to get filename)
        doc_info = get_document(doc_id, user_id)
        if not doc_info:
            self.logger.error(f"index_document: document not found for doc_id={doc_id}, user_id={user_id}")
            return False, "Document not found"

        filename = doc_info["filename"]
        file_ext = doc_info["file_ext"]

        # 0. Identical bytes are already indexed somewhere: their chunks are the
        #    same chunks, so copy the vectors instead of embedding them again.
        reused = self._reuse_indexed_content(user_id, doc_id, doc_info)
        if reused is not None:
            return reused

        # 1. Extract text
        text = extract_text_from_file(file_path)
        if not text:
            self.logger.error(f"index_document: failed to extract text from {file_path}")
            return False, "Failed to extract text from document"
        self.logger.info(f"index_document: extracted {len(text)} characters from {file_path}")

        # 2. Chunk text based on strategy
        if self.chunk_strategy == "structured":
            chunks = chunk_text_structured(text, self.chunk_size, self.chunk_overlap)
        elif self.chunk_strategy == "recursive":
            chunks = chunk_text_recursive(text, self.chunk_size, self.chunk_overlap)
        else:
            chunks = chunk_text(text, self.chunk_size, self.chunk_overlap)
        if not chunks:
            self.logger.error(f"index_document: no text chunks generated from {file_path}")
            return False, "No text chunks generated"
        self.logger.info(f"index_document: generated {len(chunks)} chunks")

        # 3. A re-index may produce a different chunk count: drop the previous
        #    points first, otherwise higher-index chunks survive as orphans.
        try:
            self.qdrant_client.delete(
                collection_name=self._get_collection_name(user_id),
                points_selector=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                ),
            )
        except Exception as exc:  # noqa: BLE001 — nothing to delete is fine
            self.logger.debug(f"re-index cleanup skipped for {doc_id}: {exc}")

        # 4. Get embeddings for all chunks using batch API (more efficient)
        embeddings = self._get_batch_embeddings(chunks)

        # Check for failed embeddings
        for idx, emb in enumerate(embeddings):
            if emb is None:
                self.logger.error(f"index_document: failed to get embedding for chunk {idx}")
                return False, "Failed to get embedding for a chunk"
            # Check for empty embeddings (vector dimension 0)
            if len(emb) == 0:
                self.logger.error(f"index_document: empty embedding vector for chunk {idx}")
                return False, "Empty embedding vector for a chunk"

        self.logger.info(f"index_document: obtained embeddings for all {len(chunks)} chunks")

        # 4. Prepare points with valid UUIDs as IDs and filename in payload
        points = []
        for idx, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=False)):
            # Generate a deterministic UUID based on doc_id and chunk index
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{idx}"))
            point = models.PointStruct(
                id=point_id,
                vector=emb,  # type: ignore[arg-type]
                payload={
                    "doc_id": doc_id,
                    "user_id": user_id,
                    "chunk_index": idx,
                    "text": chunk,
                    # Add filename metadata for better context
                    "filename": filename,
                    "file_ext": file_ext,
                },
            )
            points.append(point)

        # 5. Ensure collection exists and upsert
        try:
            self._ensure_collection(user_id)
            collection_name = self._get_collection_name(user_id)
            self.qdrant_client.upsert(collection_name=collection_name, points=points)
            self.logger.info(f"index_document: upserted {len(points)} points for doc_id={doc_id}")
            self._store_chunks(user_id, doc_id, filename, chunks)
            return True, f"Indexed {len(chunks)} chunks"
        except Exception as e:
            self.logger.error(f"Error during upsert: {e}")
            return False, f"Qdrant error: {str(e)}"

    def _reuse_indexed_content(self, user_id: str, doc_id: str, doc_info: dict) -> tuple[bool, str] | None:
        """Copy the vectors of an already-indexed document with identical bytes.

        Content addressing gives two documents the same SHA-256, so their chunks
        are identical and re-embedding them would produce the same vectors at
        full cost. The source's points are copied into this user's collection
        under this document's ``doc_id`` and payload; the source keeps its own.

        Returns ``None`` when there is nothing to reuse — no twin, a different
        embedding model, or no stored vectors — so the caller falls back to
        ordinary indexing rather than failing.
        """
        sha = (doc_info or {}).get("blob_sha256")
        if not sha:
            return None
        source = find_indexed_document_by_blob(sha, doc_id)
        if not source:
            return None

        config = get_model_config("embedding")
        model_name = config.get("model_name", "unknown") if config else "unknown"
        if source.get("embedding_model") != model_name:
            self.logger.info(
                f"reuse: {doc_id} has the same bytes as {source['id']} but a different embedding model "
                f"({source.get('embedding_model')} != {model_name}); indexing normally"
            )
            return None

        try:
            points = self._scroll_document_points(source["user_id"], source["id"])
        except Exception as exc:  # noqa: BLE001 — a failed copy falls back to indexing
            self.logger.warning(f"reuse: cannot read vectors of {source['id']}: {exc}")
            return None
        if not points:
            self.logger.info(f"reuse: {source['id']} has no stored vectors; indexing normally")
            return None

        ordered = sorted(points, key=lambda point: (point.payload or {}).get("chunk_index", 0))
        chunks = [(point.payload or {}).get("text", "") for point in ordered]
        try:
            self._ensure_collection(user_id)
            self.qdrant_client.upsert(
                collection_name=self._get_collection_name(user_id),
                points=self._copy_points(ordered, user_id, doc_id, doc_info),
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"reuse: copy into {user_id} failed for {doc_id}: {exc}")
            return None

        self._store_chunks(user_id, doc_id, filename=doc_info["filename"], chunks=chunks)
        self.logger.info(
            f"reuse: copied {len(points)} vectors of {source['id']} ({source['user_id']}) to {doc_id} ({user_id})"
        )
        return True, f"Indexed {len(points)} chunks (reused embeddings)"

    def _scroll_document_points(self, user_id: str, doc_id: str) -> list:
        """Read every stored point of one document, vectors included."""
        points: list = []
        offset = None
        while True:
            batch, offset = self.qdrant_client.scroll(
                collection_name=self._get_collection_name(user_id),
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                ),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            points.extend(batch)
            if offset is None:
                return points

    def _copy_points(self, points: list, user_id: str, doc_id: str, doc_info: dict) -> list:
        """Rebuild the source's points as this document's own points."""
        copied = []
        for position, point in enumerate(points):
            payload = dict(point.payload or {})
            index = payload.get("chunk_index", position)
            payload.update(
                {
                    "doc_id": doc_id,
                    "user_id": user_id,
                    "filename": doc_info["filename"],
                    "file_ext": doc_info["file_ext"],
                }
            )
            copied.append(
                models.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{index}")),
                    vector=point.vector,
                    payload=payload,
                )
            )
        return copied

    def delete_document(self, doc_id: str, user_id: str) -> bool:
        """Delete all points belonging to a document from the index."""
        if not self.check_availability():
            return False
        collection_name = self._get_collection_name(user_id)
        try:
            self.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                ),
            )
            self._delete_chunks(doc_id, user_id)
            self.logger.info(f"delete_document: deleted vectors for doc_id={doc_id}")
            return True
        except Exception as e:
            # If the collection does not exist, there is nothing to delete -> treat as success
            if "Not found" in str(e) or "doesn't exist" in str(e):
                return True
            self.logger.error(f"Failed to delete document {doc_id} from index: {e}")
            return False

    def _store_chunks(self, user_id: str, doc_id: str, filename: str, chunks: list[str]) -> None:
        """Mirror the chunks into PostgreSQL for the lexical half of hybrid search."""
        try:
            from app.database import get_db

            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM rag_chunks WHERE user_id = %s AND doc_id = %s", (user_id, doc_id))
                for index, chunk in enumerate(chunks):
                    cursor.execute(
                        "INSERT INTO rag_chunks (user_id, doc_id, chunk_index, filename, text, lemmas) "
                        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (user_id, doc_id, index, filename, chunk, lemmatize(chunk)),
                    )
        except Exception as exc:  # noqa: BLE001 — the lexical index must never break indexing
            self.logger.warning(f"rag_chunks mirror failed for doc_id={doc_id}: {exc}")

    def _delete_chunks(self, doc_id: str, user_id: str) -> None:
        try:
            from app.database import get_db

            with get_db() as conn:
                conn.cursor().execute("DELETE FROM rag_chunks WHERE user_id = %s AND doc_id = %s", (user_id, doc_id))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"rag_chunks delete failed for doc_id={doc_id}: {exc}")

    def _lexical_search(
        self,
        user_id: str,
        query: str,
        limit: int,
        doc_ids: list[str] | None = None,
    ) -> list[dict]:
        """BM25 over the lemmatized chunks — the lexical half of hybrid search.

        ``ts_rank_cd`` scored term coverage without IDF and without length
        normalisation, and the rarity gate compensated by dropping common words
        (it also had to guess at the stemmer, hence the prefix queries). BM25
        carries both, so the gate and the prefix tricks are gone: every lemma of
        the query is scored against the candidates that contain at least one.
        """
        from app.database import get_db

        terms = list(dict.fromkeys(lemmatize(query)))
        if not terms:
            return []

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS n, coalesce(avg(cardinality(lemmas)), 0) AS avgdl "
                "FROM rag_chunks WHERE user_id = %s AND lemmas IS NOT NULL",
                (user_id,),
            )
            stats = cursor.fetchone() or {}
            n_chunks = int(stats.get("n") or 0)
            average_length = float(stats.get("avgdl") or 0)
            if not n_chunks or average_length <= 0:
                return []
            cursor.execute(
                "SELECT term, count(*) AS df FROM rag_chunks rc, unnest(rc.lemmas) AS term "
                "WHERE rc.user_id = %s AND term = ANY(%s) GROUP BY term",
                (user_id, terms),
            )
            document_frequency = {row["term"]: int(row["df"]) for row in cursor.fetchall()}

            sql = (
                "SELECT doc_id, chunk_index, filename, text, lemmas FROM rag_chunks "
                "WHERE user_id = %s AND lemmas && %s"
            )
            params: list = [user_id, terms]
            if doc_ids:
                sql += " AND doc_id = ANY(%s)"
                params.append(list(doc_ids))
            sql += " LIMIT %s"
            params.append(max(limit * 20, 200))
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        scored: list[tuple[float, dict]] = []
        for row in rows:
            score = bm25_score(
                row["lemmas"] or [], terms, document_frequency, n_chunks, average_length
            )
            if score > 0:
                scored.append((score, {**dict(row), "rank": score}))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [row for _, row in scored[:limit]]

    def search(
        self,
        user_id: str,
        query: str,
        top_k: int | None = None,
        file_coverage: bool = False,
        coverage_max_files: int | None = None,
        doc_ids: list[str] | None = None,
        lang: str = "ru",
    ) -> tuple[list[dict], list[float]]:
        """
        Search for relevant chunks based on query.
        Returns tuple of (chunk_dicts with metadata, scores).

        Simplified: single direct query to Qdrant.

        When ``doc_ids`` is a non-empty list, the search is scoped to those
        documents only (the caller attached them to the request) and the
        file-coverage pass is limited to the same scope.

        When ``file_coverage`` is True, the query fetches ``top_k`` relevance
        hits plus a coverage allowance, and the first chunk of every document not
        yet represented is appended from that extra allowance (marked with
        ``payload['coverage'] = True``, to survive threshold filtering). Both
        numbers come from :mod:`app.rag_budget` when the caller does not pin
        them: the allowance is what the answering model's window holds beyond the
        relevance half, so breadth follows the hardware instead of a constant.
        """
        if not self.check_availability():
            return [], []
        from flask import has_app_context

        from app.rag_budget import retrieval_budget

        # How many documents the request can search decides the split between
        # relevance depth and breadth; the same list is reused for the count later.
        scope_ids = [str(d) for d in (doc_ids or []) if d]
        # The corpus size only matters when coverage is on: it decides how much of
        # the fragment room goes to breadth. Without it the search must not touch
        # the documents table at all.
        corpus_documents: int | None = None
        if file_coverage:
            try:
                documents = get_user_documents(user_id, include_shared=True) or []
                if scope_ids:
                    documents = [d for d in documents if str(d.get("id")) in set(scope_ids)]
                corpus_documents = len([d for d in documents if d.get("index_status") == INDEX_STATUS_INDEXED])
            except Exception as exc:  # noqa: BLE001 — a count is not worth a failed search
                self.logger.debug(f"search: could not count the corpus: {exc}")
        budget = retrieval_budget(
            current_app if has_app_context() else None,
            lang,
            corpus_documents=corpus_documents,
        )
        if top_k is None:
            top_k = budget.top_k
        if coverage_max_files is None:
            coverage_max_files = budget.coverage_files
        # The allowance is fetched with the same query: one Qdrant round trip
        # instead of a per-document query for every uncovered file. The pool is
        # deeper than the allowance because a single large document can occupy
        # many consecutive hits — measured, a 2.6 MB PDF took 7 of 20 slots and
        # starved the coverage pass of material to pick from.
        fetch_limit = top_k + (coverage_max_files * COVERAGE_POOL_FACTOR if file_coverage else 0)
        self.last_coverage = {"covered": 0, "unrepresented": 0, "budget": budget}
        # ``None`` means "no scope"; an explicit empty list means "the scope
        # matched nothing", which must stay empty instead of searching everything.
        if doc_ids is not None and not scope_ids:
            return [], []
        scope = scope_ids

        # Get embedding directly from query
        query_emb = self._get_embedding(query)
        if query_emb is None:
            self.logger.warning(f"Failed to get embedding for query: {query[:50]}...")
            return [], []

        # The caller's own collection plus every collection holding a document
        # shared with them: sharing is an access right, not a copy, so the points
        # never move and the owner's index stays the single source.
        scopes = self._search_scopes(user_id, scope if doc_ids is not None else None)
        search_result = []
        for owner, owner_scope in scopes:
            must: list[models.FieldCondition] = [
                models.FieldCondition(key="user_id", match=models.MatchValue(value=owner))
            ]
            if owner_scope:
                must.append(models.FieldCondition(key="doc_id", match=models.MatchAny(any=owner_scope)))
            try:
                result = self.qdrant_client.query_points(
                    collection_name=self._get_collection_name(owner),
                    query=query_emb,
                    query_filter=models.Filter(must=must),
                    limit=fetch_limit,
                )
                search_result.extend(result.points)
            except Exception as e:
                # One owner's collection is not the caller's problem: it may be
                # absent or being recreated. Keep the scopes that answered.
                self.logger.warning(f"Search failed in the collection of {owner} for '{query[:30]}...': {e}")
        search_result.sort(key=lambda hit: hit.score, reverse=True)
        search_result = search_result[:fetch_limit]

        chunks = [hit.payload for hit in search_result]
        scores = [hit.score for hit in search_result]

        if self.hybrid_enabled:
            lexical: list[dict] = []
            for owner, owner_scope in scopes:
                lexical.extend(self._lexical_search(owner, query, self.hybrid_candidates, owner_scope))
            fused = rrf_fuse(
                [chunks, lexical],
                key=lambda item: (str(item.get("doc_id") or ""), int(item.get("chunk_index") or 0)),
                k=self.rrf_k,
            )[:fetch_limit]
            if fused:
                best = fused[0][1]
                # Relative score in [0.5, 1]: the fused order is what matters,
                # and the caller's absolute cosine threshold must not drop it.
                chunks = [item for item, _score in fused]
                scores = [0.5 + 0.5 * (score / best) if best else 0.0 for _item, score in fused]
                self.logger.info(
                    f"search: hybrid fused {len(chunks)} chunks "
                    f"(dense={len(search_result)}, lexical={len(lexical)})"
                )

        if self.rerank_enabled and len(chunks) > 1:
            from app.reranker import rerank as rerank_scores

            pool = chunks[: self.rerank_candidates]
            texts = [str(chunk.get("text") or "") for chunk in pool]
            reranked = rerank_scores(
                query,
                texts,
                current_app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080"),
                timeout=self.rerank_timeout,
            )
            if reranked is not None:
                order = sorted(range(len(pool)), key=lambda index: reranked[index], reverse=True)
                dense = scores[: len(pool)]
                chunks = [pool[index] for index in order] + chunks[len(pool) :]
                # The caller filters on absolute cosine scores; the reranker
                # only decides the order, so every chunk keeps the cosine value
                # it was retrieved with. Its relative scale (best = 1.0) would
                # otherwise shrink a 20-chunk context to the top two.
                scores = [dense[index] for index in order] + scores[len(pool) :]
                self.logger.info(f"search: reranked {len(pool)} candidates")

        self.logger.info(
            f"search: found {len(chunks)} chunks for query '{query[:50]}...' "
            f"(top_k={top_k}, coverage allowance={coverage_max_files if file_coverage else 0}, "
            f"window budget={budget.fragments} fragments / {budget.tokens} tokens)"
        )

        if file_coverage and chunks:
            pairs, added = apply_coverage(
                list(zip(chunks[:top_k], scores[:top_k], strict=False)),
                list(zip(chunks[top_k:], scores[top_k:], strict=False)),
                coverage_max_files,
            )
            chunks = [chunk for chunk, _score in pairs]
            scores = [score for _chunk, score in pairs]
            unrepresented = 0
            covered = {str(c.get("doc_id")) for c in chunks if c.get("doc_id")}
            # A document that the extended fetch did not reach either stays out;
            # saying so is the point, the old code refused coverage entirely when
            # the count exceeded its cap and looked like a complete answer.
            try:
                docs = get_user_documents(user_id) or []
                if scope:
                    docs = [d for d in docs if str(d.get("id")) in set(scope)]
                indexed = {str(d.get("id")) for d in docs if d.get("index_status") == INDEX_STATUS_INDEXED}
                unrepresented = max(0, len(indexed - covered))
            except Exception as exc:  # noqa: BLE001 — a count is not worth a failed answer
                self.logger.debug(f"search: could not count unrepresented documents: {exc}")
            self.last_coverage = {"covered": added, "unrepresented": unrepresented, "budget": budget}
            self.logger.info(
                f"search: coverage added {added} document(s); {unrepresented} indexed document(s) "
                f"stay out of this context"
            )

        return chunks, scores

    def _estimate_tokens(self, text: str) -> int:
        """Estimate tokens using configured characters per token."""
        token_chars = current_app.config.get("TOKEN_CHARS", 3)
        return estimate_tokens(text, token_chars)

    def _build_context_prompt(self, history: list[dict[str, str]], lang: str = "ru") -> str:
        """Format conversation history into a string."""
        return build_context_prompt(history, lang)

    def generate_answer(
        self,
        user_id: str,
        query: str,
        session_id: str,
        lang: str = "ru",
        threshold: float | None = None,
        response_style: str = "neutral",
        token_callback: Callable[[str], None] | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        """Full RAG answer: search + call reasoning model with context."""
        # 1. Retrieve relevant chunks
        chunks, scores = self.search(user_id, query)
        if not chunks:
            # No relevant documents - return None to trigger fallback
            self.logger.info(f"No relevant documents found for query: {query[:50]}...")
            return None, None, None

        # Log all retrieved chunks with their scores (debug level)
        if self.logger.isEnabledFor(logging.DEBUG):
            for i, (chunk_data, score) in enumerate(zip(chunks, scores, strict=False)):
                text = chunk_data.get("text", chunk_data) if isinstance(chunk_data, dict) else chunk_data
                preview = text[:200].replace("\n", " ").strip() + "..." if len(text) > 200 else text.replace("\n", " ")
                self.logger.debug(f"RAG chunk[{i}] score={score:.4f} preview='{preview}'")

        # Determine threshold for cosine similarity filtering
        # Default to RAG_RELEVANCE_THRESHOLD_REASONING (0.2) if not specified
        if threshold is None:
            threshold = current_app.config.get("RAG_RELEVANCE_THRESHOLD_REASONING", 0.2)

        # Filter chunks by score
        filtered = [(chunk, score) for chunk, score in zip(chunks, scores, strict=False) if score >= threshold]
        if not filtered:
            self.logger.info(f"RAG: no chunks with score >= {threshold} for query: {query[:50]}...")
            # Log summary of scores for analysis
            scores_str = ", ".join([f"{s:.4f}" for s in scores])
            self.logger.info(f"RAG scores for query: {scores_str}")
            return None, None, None

        # Get localized label for "Source"
        with current_app.app_context(), force_locale(lang):
            source_label = _("Source")

        # 2. Prepare context string WITH filename sources
        context_parts = []
        for chunk_data, _score in filtered:
            # Extract filename and text from chunk metadata
            filename = chunk_data.get("filename", "unknown") if isinstance(chunk_data, dict) else "unknown"
            text = chunk_data.get("text", chunk_data) if isinstance(chunk_data, dict) else chunk_data
            # One header per chunk: source file, no relevance score
            context_parts.append(format_chunk_entry(source_label, filename, text))
        context = "\n\n".join(context_parts)

        # Logging the structure of the RAG context WITH RELEVANCE SCORES
        chunk_sizes = [len(c.get("text", c) if isinstance(c, dict) else c) for c, _ in filtered]
        self.logger.info(
            f"RAG DEBUG: query='{query[:60]}...', "
            f"chunks_found={len(chunks)}, filtered_chunks={len(filtered)}, "
            f"chunk_sizes_chars={chunk_sizes}, "
            f"scores={[score for _, score in filtered]}, "
            f"total_context_chars={len(context)}, "
            f"estimated_context_tokens={self._estimate_tokens(context)}"
        )
        # Output of previews of the first 10 chunks with relevance scores
        for i, (chunk_data, score) in enumerate(filtered[:10]):
            text = chunk_data.get("text", chunk_data) if isinstance(chunk_data, dict) else chunk_data
            preview = text[:200].replace("\n", " ").strip() + "..." if len(text) > 200 else text.replace("\n", " ")
            self.logger.debug(f"RAG DEBUG: chunk[{i}] score={score:.4f} preview='{preview}'")

        # 3. Get conversation history (with token limit)
        # Estimate actual token counts for query and context
        query_tokens = self._estimate_tokens(query)
        context_tokens = self._estimate_tokens(context)

        # Get reasoning model config
        reasoning_config = get_model_config("reasoning")
        if not reasoning_config:
            return None, "Reasoning model configuration missing", None
        max_context_tokens = reasoning_config.get("context_length", 40960)
        self.logger.info(f"RAG: reasoning context_length from config: {max_context_tokens}")

        # The document share and the history share are split by measured need
        # (app/context_budget.py), not by two percentages multiplied together: a
        # CPU host with an 8 192-token window used to grant documents 30 % of it
        # regardless of how small the corpus was.
        from app.context_budget import cap_share, context_budget
        from app.utils import load_prompt_template

        template_text = load_prompt_template("rag.template", lang) or ""
        template_overhead = self._estimate_tokens(template_text)
        history_need = 0
        if session_id:
            history_need = sum(
                self._estimate_tokens(str(message.get("content") or ""))
                for message in (get_session_text_history(session_id, 10_000) or [])
            )
        budget = context_budget(max_context_tokens, template_overhead + query_tokens)
        documents_tokens, history_tokens = budget.split(history_need=history_need, document_need=context_tokens)
        max_context_tokens_limit = cap_share(
            documents_tokens,
            budget.available,
            int(current_app.config.get("RAG_CONTEXT_PERCENT") or 0),
        )

        if context_tokens > max_context_tokens_limit:
            # Trim chunks from the end (lowest relevance) until under limit
            original_count = len(filtered)
            while context_tokens > max_context_tokens_limit and len(filtered) > 1:
                filtered.pop()  # Remove lowest-relevance chunk
                # Rebuild context
                with current_app.app_context(), force_locale(lang):
                    source_label = _("Source")
                context_parts = []
                for chunk_data, _score in filtered:
                    filename = chunk_data.get("filename", "unknown") if isinstance(chunk_data, dict) else "unknown"
                    text = chunk_data.get("text", chunk_data) if isinstance(chunk_data, dict) else chunk_data
                    context_parts.append(format_chunk_entry(source_label, filename, text))
                context = "\n\n".join(context_parts)
                context_tokens = self._estimate_tokens(context)
            self.logger.info(
                f"RAG: trimmed context from {original_count} to {len(filtered)} chunks "
                f"({context_tokens}/{max_context_tokens_limit} tokens, "
                f"window {max_context_tokens}, history granted {history_tokens})"
            )

        remaining_for_history = history_tokens
        history_str = ""
        if remaining_for_history > 0 and session_id:
            history_msgs = get_session_text_history(session_id, remaining_for_history)
            history_str = self._build_context_prompt(history_msgs, lang)

        # 4. Get current time and response language
        current_time_str = get_current_time_in_timezone(current_app)
        response_language = response_language_name(lang)

        # 5. Format prompt using template
        style_instruction = get_style_instruction(lang, response_style)

        prompt = format_prompt(
            "rag.template",
            {
                "current_time_str": current_time_str,
                "response_language": response_language,
                "conversation_history": history_str,
                "context": context,
                "user_query": query,
                "response_style": style_instruction,
            },
            lang=lang,
        )
        if not prompt:
            self.logger.error("Failed to load rag.template")
            return None, "Error loading prompt template", None

        # 6. Call reasoning model
        # Brief pause to let CUDA finish deallocation after model unload — prevents 502
        time.sleep(3)
        model_name = reasoning_config.get("model_name", "unknown")
        if token_callback:
            full_response = ""
            for token in self.llamacpp.chat_stream(
                [{"role": "user", "content": prompt}], model_type="reasoning", lang=lang
            ):
                full_response += token
                token_callback(token)
            return full_response, None, model_name

        reasoning_module = current_app.modules.get("base")  # type: ignore[attr-defined]
        if not reasoning_module:
            return None, "Reasoning module unavailable", None
        response = reasoning_module.call_llamacpp(
            [{"role": "user", "content": prompt}], model_type="reasoning", lang=lang
        )
        return response, None, model_name

    def _get_embedding(self, text: str) -> list[float] | None:
        """Get embedding vector from llama-server via OpenAI-compatible /v1/embeddings.

        Retries once on transient failures (e.g. llama-swap "group is shutting
        down" during a config reload) — a single failed request must not
        silently convert a RAG route into context-less reasoning.
        """
        import time as time_module

        for attempt in (1, 2):
            embeddings = self.llamacpp.get_embeddings([text], model_type="embedding")
            if embeddings and len(embeddings) > 0 and embeddings[0] is not None:
                emb = embeddings[0]
                self.logger.debug(f"_get_embedding: got embedding of length {len(emb)} (attempt {attempt})")
                return emb  # type: ignore[no-any-return]
            if attempt == 1:
                self.logger.warning("_get_embedding: no embedding returned — retrying once")
                time_module.sleep(2)
        self.logger.warning("_get_embedding: no embedding returned after retry")
        return None

    def _get_batch_embeddings(self, texts: list[str]) -> list[list[float] | None]:
        """Get embeddings for multiple texts using llama.cpp batch API.
        Returns list of embeddings (None for failed requests).
        Retries on transient failures (e.g. server restart after config change).
        """
        import time as time_module

        max_retries = 3
        for attempt in range(max_retries):
            self.logger.info(
                f"RAG: _get_batch_embeddings called with {len(texts)} texts (attempt {attempt + 1}/{max_retries})"
            )
            embeddings = self.llamacpp.get_embeddings(texts, model_type="embedding")
            self.logger.info(f"RAG: get_embeddings returned {type(embeddings)}")

            if embeddings is not None:
                # Ensure we have the same number of embeddings as input texts
                if len(embeddings) != len(texts):
                    self.logger.warning(f"Embedding count mismatch: expected {len(texts)}, got {len(embeddings)}")
                    while len(embeddings) < len(texts):
                        embeddings.append(None)
                return embeddings[: len(texts)]  # type: ignore[no-any-return]

            if attempt < max_retries - 1:
                delay = 5 * (attempt + 1)
                self.logger.warning(f"Embedding failed, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                time_module.sleep(delay)

        self.logger.error("Failed to get embeddings after all retries")
        return [None] * len(texts)
