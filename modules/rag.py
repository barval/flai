# modules/rag.py
import logging
import time
import uuid
from collections.abc import Callable

from flask import current_app
from flask_babel import force_locale
from flask_babel import gettext as _
from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.db import get_document, get_session_text_history
from app.llamacpp_client import LlamaCppClient
from app.model_config import get_model_config
from app.utils import (
    build_context_prompt,
    chunk_text,
    chunk_text_recursive,
    estimate_tokens,
    extract_text_from_file,
    format_prompt,
    get_current_time_in_timezone,
)


class RagModule:
    """Module for Retrieval-Augmented Generation using Qdrant and llama.cpp embeddings."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.qdrant_client = None
        self.llamacpp = LlamaCppClient(app)
        self.available = False
        self.collection_name_prefix = "user_"
        self.chunk_size = 500
        self.chunk_overlap = 50
        self.chunk_strategy = "fixed"
        self.top_k = 20
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

        app.logger.info(f"RagModule: loaded chunk_size = {self.chunk_size}")
        app.logger.info(f"RagModule: loaded chunk_overlap = {self.chunk_overlap}")
        app.logger.info(f"RagModule: loaded chunk_strategy = {self.chunk_strategy}")
        app.logger.info(f"RagModule: loaded top_k = {self.top_k}")

        if not qdrant_url:
            app.logger.warning("QDRANT_URL not set, RAG module disabled")
            self.available = False
            return
        try:
            self.qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
            # Test connection
            self.qdrant_client.get_collections()
            self.available = True
            app.logger.info(f"RagModule initialized with Qdrant at {qdrant_url}, top_k={self.top_k}")
        except Exception as e:
            self.available = False
            app.logger.error(f"Failed to connect to Qdrant: {e}")

    def _get_embedding_model(self) -> str | None:
        """Retrieve embedding model name from database."""
        config = get_model_config("embedding")
        return config.get("model_name") if config else None

    def _get_embedding_url(self) -> str | None:
        """Retrieve service URL for embedding from database."""
        config = get_model_config("embedding")
        return config.get("service_url") if config else None

    def _get_collection_name(self, user_id: str) -> str:
        """Return collection name for a specific user."""
        return f"{self.collection_name_prefix}{user_id}"

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
        if not self.available:
            return False, "RAG service unavailable"
        self.logger.info(f"index_document: starting for doc_id={doc_id}, file_path={file_path}")

        # Get document metadata from DB (to get filename)
        doc_info = get_document(doc_id, user_id)
        if not doc_info:
            self.logger.error(f"index_document: document not found for doc_id={doc_id}, user_id={user_id}")
            return False, "Document not found"

        filename = doc_info["filename"]
        file_ext = doc_info["file_ext"]

        # 1. Extract text
        text = extract_text_from_file(file_path)
        if not text:
            self.logger.error(f"index_document: failed to extract text from {file_path}")
            return False, "Failed to extract text from document"
        self.logger.info(f"index_document: extracted {len(text)} characters from {file_path}")

        # 2. Chunk text based on strategy
        if self.chunk_strategy == "recursive":
            chunks = chunk_text_recursive(text, self.chunk_size, self.chunk_overlap)
        else:
            chunks = chunk_text(text, self.chunk_size, self.chunk_overlap)
        if not chunks:
            self.logger.error(f"index_document: no text chunks generated from {file_path}")
            return False, "No text chunks generated"
        self.logger.info(f"index_document: generated {len(chunks)} chunks")

        # 3. Get embeddings for all chunks using batch API (more efficient)
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
            return True, f"Indexed {len(chunks)} chunks"
        except Exception as e:
            self.logger.error(f"Error during upsert: {e}")
            return False, f"Qdrant error: {str(e)}"

    def delete_document(self, doc_id: str, user_id: str) -> bool:
        """Delete all points belonging to a document from the index."""
        if not self.available:
            return False
        collection_name = self._get_collection_name(user_id)
        try:
            self.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                ),
            )
            self.logger.info(f"delete_document: deleted vectors for doc_id={doc_id}")
            return True
        except Exception as e:
            # If the collection does not exist, there is nothing to delete -> treat as success
            if "Not found" in str(e) or "doesn't exist" in str(e):
                return True
            self.logger.error(f"Failed to delete document {doc_id} from index: {e}")
            return False

    def search(self, user_id: str, query: str, top_k: int | None = None) -> tuple[list[dict], list[float]]:
        """
        Search for relevant chunks based on query.
        Returns tuple of (chunk_dicts with metadata, scores).

        Simplified: single direct query to Qdrant.
        """
        if not self.available:
            return [], []
        top_k = top_k or self.top_k

        # Get embedding directly from query
        query_emb = self._get_embedding(query)
        if query_emb is None:
            self.logger.warning(f"Failed to get embedding for query: {query[:50]}...")
            return [], []

        collection_name = self._get_collection_name(user_id)
        try:
            search_result = self.qdrant_client.search(
                collection_name=collection_name,
                query_vector=query_emb,
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))]
                ),
                limit=top_k,
            )
        except Exception as e:
            self.logger.warning(f"Search failed for query '{query[:30]}...': {e}")
            return [], []

        chunks = [hit.payload for hit in search_result]
        scores = [hit.score for hit in search_result]

        self.logger.info(f"search: found {len(chunks)} chunks for query '{query[:50]}...' (top_k={top_k})")

        for i, chunk in enumerate(chunks):
            text_preview = chunk.get("text", "")[:100].replace("\n", " ")
            self.logger.info(
                f"  chunk[{i}]: doc_id={chunk.get('doc_id', '?')}, score={scores[i]:.4f}, text='{text_preview}...'"
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
        for chunk_data, score in filtered:
            # Extract filename and text from chunk metadata
            filename = chunk_data.get("filename", "unknown") if isinstance(chunk_data, dict) else "unknown"
            text = chunk_data.get("text", chunk_data) if isinstance(chunk_data, dict) else chunk_data
            # Add source indicator to each chunk (include score for debugging)
            context_parts.append(f"[{source_label}: {filename} (score: {score:.2f})]\n{text}")
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

        # Dynamic context limit: percentage of model's context window
        rag_context_percent = current_app.config.get("RAG_CONTEXT_PERCENT", 50)
        max_context_tokens_limit = int(max_context_tokens * rag_context_percent / 100.0)

        # Measure actual template overhead (already filled with variables)
        # We know: total_prompt = template + context + query + history
        # So: template_tokens = total_tokens_of_filled_template - context - query
        # But we don't have the filled template yet. Instead, estimate from loaded template.
        from app.utils import load_prompt_template

        template_text = load_prompt_template("rag.template", lang) or ""
        template_overhead = self._estimate_tokens(template_text)

        if context_tokens > max_context_tokens_limit:
            # Trim chunks from the end (lowest relevance) until under limit
            original_count = len(filtered)
            while context_tokens > max_context_tokens_limit and len(filtered) > 1:
                filtered.pop()  # Remove lowest-relevance chunk
                # Rebuild context
                with current_app.app_context(), force_locale(lang):
                    source_label = _("Source")
                context_parts = []
                for chunk_data, score in filtered:
                    filename = chunk_data.get("filename", "unknown") if isinstance(chunk_data, dict) else "unknown"
                    text = chunk_data.get("text", chunk_data) if isinstance(chunk_data, dict) else chunk_data
                    context_parts.append(f"[{source_label}: {filename} (score: {score:.2f})]\n{text}")
                context = "\n\n".join(context_parts)
                context_tokens = self._estimate_tokens(context)
            self.logger.info(
                f"RAG: trimmed context from {original_count} to {len(filtered)} chunks "
                f"({context_tokens}/{max_context_tokens_limit} tokens, "
                f"{rag_context_percent}% of {max_context_tokens}) to fit within model context"
            )

        # Calculate actual template overhead from the raw template file
        # (placeholders like {context}, {user_query} contribute 0 tokens,
        #  so we measure the template as-is)
        template_overhead = self._estimate_tokens(template_text)

        # Calculate available space for history
        history_percent = int(current_app.config.get("CONTEXT_HISTORY_PERCENT", 75))
        available_tokens = int(max_context_tokens * (history_percent / 100.0))
        remaining_for_history = available_tokens - query_tokens - context_tokens - template_overhead
        history_str = ""
        if remaining_for_history > 0 and session_id:
            history_msgs = get_session_text_history(session_id, remaining_for_history)
            history_str = self._build_context_prompt(history_msgs, lang)

        # 4. Get current time and response language
        current_time_str = get_current_time_in_timezone(current_app)
        response_language = "Russian" if lang == "ru" else "English"

        # 5. Format prompt using template
        style_map = {
            "ru": {
                "neutral": "Без особого стиля.",
                "academic": "Отвечай в формальном академическом стиле. Используй точную терминологию, строгие формулировки и логически структурированные аргументы. Избегай разговорных выражений. При необходимости ссылайся на факты.",
                "professional": "Отвечай в профессиональном деловом стиле. Будь чётким, конкретным и по делу. Используй ясные формулировки. Избегай лишних эмоций и воды.",
                "friendly": "Отвечай в тёплом дружеском стиле. Будь приветлив и располагай к общению. Используй естественный разговорный тон. Покажи эмпатию и заботу о пользователе. Можно использовать эмодзи, если они уместны и помогают выразить эмоцию.",
                "funny": "Отвечай с юмором и остроумием. Будь игрив и занимателен. Используй шутки, метафоры и неожиданные сравнения, но не забывай давать полезную информацию по существу вопроса. Эмодзи приветствуются, если они к месту и усиливают эффект.",
            },
            "en": {
                "neutral": "Default style.",
                "academic": "Answer in a formal academic style. Use precise terminology, rigorous wording, and logically structured arguments. Avoid colloquial expressions. Reference facts where appropriate.",
                "professional": "Answer in a professional business-like style. Be clear, specific, and to the point. Use straightforward wording. Avoid unnecessary emotions or fluff.",
                "friendly": "Answer in a warm, friendly style. Be welcoming and approachable. Use a natural conversational tone. Show empathy and care for the user. You may use emojis when they are appropriate and help convey emotion.",
                "funny": "Answer with humor and wit. Be playful and entertaining. Use jokes, metaphors, and unexpected comparisons, but still provide useful information on the topic. Emojis are welcome when they fit the context and enhance the effect.",
            },
        }
        style_instruction = style_map.get(lang, style_map["ru"]).get(response_style, style_map["ru"]["neutral"])

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
        """Get embedding vector from llama-server via OpenAI-compatible /v1/embeddings."""
        embeddings = self.llamacpp.get_embeddings([text], model_type="embedding")
        if embeddings and len(embeddings) > 0 and embeddings[0] is not None:
            emb = embeddings[0]
            self.logger.debug(f"_get_embedding: got embedding of length {len(emb)}")
            return emb  # type: ignore[no-any-return]
        self.logger.warning("_get_embedding: no embedding returned")
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
