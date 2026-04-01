# modules/rag.py
import logging
import requests
import os
import uuid
from typing import List, Dict, Optional, Tuple, Any
from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale
from qdrant_client import QdrantClient
from qdrant_client.http import models
from app.utils import extract_text_from_file, chunk_text, get_current_time_in_timezone, format_prompt, estimate_tokens, build_context_prompt
from app.db import get_session_text_history, update_document_index_status, get_document
from app.model_config import get_model_config

class RagModule:
    """Module for Retrieval-Augmented Generation using Qdrant and Ollama embeddings."""
    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.qdrant_client = None
        self.available = False
        self.collection_name_prefix = "user_"
        self.chunk_size = 500
        self.chunk_overlap = 50
        self.top_k = 10
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize module with Flask app configuration."""
        qdrant_url = app.config.get('QDRANT_URL')
        qdrant_api_key = app.config.get('QDRANT_API_KEY')
        self.chunk_size = app.config.get('RAG_CHUNK_SIZE', 500)
        self.chunk_overlap = app.config.get('RAG_CHUNK_OVERLAP', 50)
        self.top_k = app.config.get('RAG_TOP_K', 10)
        # Log the loaded top_k value for debugging
        app.logger.info(f"RagModule: loaded RAG_TOP_K = {self.top_k} from config")

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

    def _get_embedding_model(self) -> Optional[str]:
        """Retrieve embedding model name from database."""
        config = get_model_config('embedding')
        return config.get('model_name') if config else None

    def _get_embedding_url(self) -> Optional[str]:
        """Retrieve Ollama URL for embedding from database."""
        config = get_model_config('embedding')
        return config.get('ollama_url') if config else None

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
                vectors_config=models.VectorParams(size=current_dim, distance=models.Distance.COSINE)
            )
            self.logger.info(f"Created collection {collection_name} with vector size {current_dim}")

    def index_document(self, user_id: str, doc_id: str, file_path: str) -> Tuple[bool, str]:
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
        
        filename = doc_info['filename']
        file_ext = doc_info['file_ext']

        # 1. Extract text
        text = extract_text_from_file(file_path)
        if not text:
            self.logger.error(f"index_document: failed to extract text from {file_path}")
            return False, "Failed to extract text from document"
        self.logger.info(f"index_document: extracted {len(text)} characters from {file_path}")

        # 2. Chunk text
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
        
        self.logger.info(f"index_document: obtained embeddings for all {len(chunks)} chunks")

        # 4. Prepare points with valid UUIDs as IDs and filename in payload
        points = []
        for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            # Generate a deterministic UUID based on doc_id and chunk index
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{idx}"))
            point = models.PointStruct(
                id=point_id,
                vector=emb,
                payload={
                    "doc_id": doc_id,
                    "user_id": user_id,
                    "chunk_index": idx,
                    "text": chunk,
                    # Add filename metadata for better context
                    "filename": filename,
                    "file_ext": file_ext
                }
            )
            points.append(point)

        # 5. Ensure collection exists and upsert
        try:
            self._ensure_collection(user_id)
            collection_name = self._get_collection_name(user_id)
            self.qdrant_client.upsert(
                collection_name=collection_name,
                points=points
            )
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
                )
            )
            self.logger.info(f"delete_document: deleted vectors for doc_id={doc_id}")
            return True
        except Exception as e:
            # If the collection does not exist, there is nothing to delete -> treat as success
            if "Not found" in str(e) or "doesn't exist" in str(e):
                return True
            self.logger.error(f"Failed to delete document {doc_id} from index: {e}")
            return False

    def search(self, user_id: str, query: str, top_k: Optional[int] = None) -> Tuple[List[Dict], List[float]]:
        """
        Search for relevant chunks based on query.
        Returns tuple of (chunk_dicts with metadata, scores).
        """
        if not self.available:
            return [], []
        top_k = top_k or self.top_k
        query_emb = self._get_embedding(query)
        if query_emb is None:
            return [], []
        collection_name = self._get_collection_name(user_id)
        try:
            search_result = self.qdrant_client.search(
                collection_name=collection_name,
                query_vector=query_emb,
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))]
                ),
                limit=top_k
            )
            # Return full payload with metadata, not just text
            chunks = [hit.payload for hit in search_result]
            scores = [hit.score for hit in search_result]
            self.logger.info(f"search: found {len(chunks)} chunks for query '{query[:50]}...' (top_k={top_k})")
            return chunks, scores
        except Exception as e:
            self.logger.error(f"Qdrant search error: {e}")
            return [], []

    # --- Helper: token estimation (using centralized function) ---
    def _estimate_tokens(self, text: str) -> int:
        """Estimate tokens using configured characters per token."""
        token_chars = current_app.config.get('TOKEN_CHARS', 3)
        return estimate_tokens(text, token_chars)

    # --- Helper: build history string using centralized function ---
    def _build_context_prompt(self, history: List[Dict[str, str]], lang: str = 'ru') -> str:
        """Format conversation history into a string."""
        return build_context_prompt(history, lang)

    def generate_answer(self, user_id: str, query: str, session_id: str, lang: str = 'ru',
                        threshold: float = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Full RAG answer: search + call reasoning model with context, history, and role information.
        If threshold is provided, only chunks with score >= threshold are used.
        Returns (answer, error_message, model_name).
        Returns (None, None, None) if no relevant documents found.
        """
        # 1. Retrieve relevant chunks
        chunks, scores = self.search(user_id, query)
        if not chunks:
            # No relevant documents - return None to trigger fallback
            self.logger.info(f"No relevant documents found for query: {query[:50]}...")
            return None, None, None

        # Log all retrieved chunks with their scores (debug level)
        if self.logger.isEnabledFor(logging.DEBUG):
            for i, (chunk_data, score) in enumerate(zip(chunks, scores)):
                text = chunk_data.get('text', chunk_data) if isinstance(chunk_data, dict) else chunk_data
                preview = text[:200].replace('\n', ' ').strip() + '...' if len(text) > 200 else text.replace('\n', ' ')
                self.logger.debug(f"RAG chunk[{i}] score={score:.4f} preview='{preview}'")

        # Determine threshold
        if threshold is None:
            threshold = current_app.config.get('RAG_RELEVANCE_THRESHOLD_DEFAULT', 0.5)

        # Filter chunks by score
        filtered = [(chunk, score) for chunk, score in zip(chunks, scores) if score >= threshold]
        if not filtered:
            self.logger.info(f"RAG: no chunks with score >= {threshold} for query: {query[:50]}...")
            # Log summary of scores for analysis
            scores_str = ', '.join([f"{s:.4f}" for s in scores])
            self.logger.info(f"RAG scores for query: {scores_str}")
            return None, None, None

        # Get localized label for "Source"
        with current_app.app_context():
            with force_locale(lang):
                source_label = _('Source')

        # 2. Prepare context string WITH filename sources
        context_parts = []
        for chunk_data, score in filtered:
            # Extract filename and text from chunk metadata
            filename = chunk_data.get('filename', 'unknown') if isinstance(chunk_data, dict) else 'unknown'
            text = chunk_data.get('text', chunk_data) if isinstance(chunk_data, dict) else chunk_data
            # Add source indicator to each chunk (include score for debugging)
            context_parts.append(f"[{source_label}: {filename} (score: {score:.2f})]\n{text}")
        context = "\n\n".join(context_parts)

        # Logging the structure of the RAG context WITH RELEVANCE SCORES
        chunk_sizes = [len(c.get('text', c) if isinstance(c, dict) else c) for c, _ in filtered]
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
            text = chunk_data.get('text', chunk_data) if isinstance(chunk_data, dict) else chunk_data
            preview = text[:200].replace('\n', ' ').strip() + '...' if len(text) > 200 else text.replace('\n', ' ')
            self.logger.debug(
                f"RAG DEBUG: chunk[{i}] score={score:.4f} preview='{preview}'"
            )

        # 3. Get conversation history (with token limit)
        # Estimate token count for context and query
        query_tokens = self._estimate_tokens(query)
        context_tokens = self._estimate_tokens(context)
        template_overhead = 800  # rough estimate for template text + instructions
        # Get reasoning model config from DB
        reasoning_config = get_model_config('reasoning')
        if not reasoning_config:
            return None, "Reasoning model configuration missing", None
        max_context_tokens = reasoning_config.get('context_length', 40960)
        history_percent = int(current_app.config.get('CONTEXT_HISTORY_PERCENT', 75))
        available_tokens = int(max_context_tokens * (history_percent / 100.0))
        remaining_for_history = available_tokens - query_tokens - context_tokens - template_overhead
        history_str = ""
        if remaining_for_history > 0 and session_id:
            history_msgs = get_session_text_history(session_id, remaining_for_history)
            history_str = self._build_context_prompt(history_msgs, lang)

        # 4. Get current time and response language
        current_time_str = get_current_time_in_timezone(current_app)
        response_language = 'Russian' if lang == 'ru' else 'English'

        # 5. Format prompt using template
        prompt = format_prompt('rag.template', {
            'current_time_str': current_time_str,
            'response_language': response_language,
            'conversation_history': history_str,
            'context': context,
            'user_query': query
        }, lang=lang)
        if not prompt:
            self.logger.error("Failed to load rag.template")
            return None, "Error loading prompt template", None

        # 6. Call reasoning model
        reasoning_module = current_app.modules.get('base')
        if not reasoning_module:
            return None, "Reasoning module unavailable", None
        response = reasoning_module.call_ollama(
            [{'role': 'user', 'content': prompt}],
            model_type='reasoning',
            lang=lang
        )
        model_name = reasoning_config.get('model_name', 'unknown')
        return response, None, model_name

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding vector from Ollama using configured embedding model."""
        embedding_config = get_model_config('embedding')
        if not embedding_config:
            self.logger.error("No embedding model configuration found")
            return None
        embedding_model = embedding_config.get('model_name')
        if not embedding_model:
            self.logger.error("No embedding model configured in database")
            return None
        ollama_url = embedding_config.get('ollama_url')
        if not ollama_url:
            ollama_url = 'http://ollama:11434'
            self.logger.warning(f"No ollama_url for embedding, using default {ollama_url}")
        try:
            response = requests.post(
                f"{ollama_url}/api/embeddings",
                json={"model": embedding_model, "prompt": text},
                timeout=30
            )
            if response.status_code == 200:
                emb = response.json()["embedding"]
                self.logger.debug(f"_get_embedding: got embedding of length {len(emb)}")
                return emb
            else:
                self.logger.error(f"Ollama embeddings API returned status {response.status_code}")
                return None
        except Exception as e:
            self.logger.error(f"Error getting embedding: {e}")
            return None

    def _get_batch_embeddings(self, texts: List[str], batch_size: int = 10) -> List[Optional[List[float]]]:
        """Get embeddings for multiple texts using batched API calls.
        This is more efficient than calling _get_embedding for each text.
        Returns list of embeddings (None for failed requests).
        """
        embedding_config = get_model_config('embedding')
        if not embedding_config:
            self.logger.error("No embedding model configuration found")
            return [None] * len(texts)
        
        embedding_model = embedding_config.get('model_name')
        if not embedding_model:
            self.logger.error("No embedding model configured in database")
            return [None] * len(texts)
        
        ollama_url = embedding_config.get('ollama_url')
        if not ollama_url:
            ollama_url = 'http://ollama:11434'
        
        all_embeddings = []
        
        # Process in batches
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            try:
                response = requests.post(
                    f"{ollama_url}/api/embeddings",
                    json={"model": embedding_model, "prompts": batch},
                    timeout=60
                )
                if response.status_code == 200:
                    result = response.json()
                    # Ollama may return single embedding or list of embeddings
                    if 'embeddings' in result:
                        all_embeddings.extend(result['embeddings'])
                    elif 'embedding' in result:
                        all_embeddings.append(result['embedding'])
                    else:
                        self.logger.warning(f"Unexpected embedding response format")
                        all_embeddings.extend([None] * len(batch))
                else:
                    self.logger.error(f"Ollama batch embeddings API returned status {response.status_code}")
                    all_embeddings.extend([None] * len(batch))
            except Exception as e:
                self.logger.error(f"Error getting batch embeddings: {e}")
                all_embeddings.extend([None] * len(batch))

        return all_embeddings