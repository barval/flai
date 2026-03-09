# modules/rag.py
import logging
import requests
import os
import uuid
from flask import current_app
from qdrant_client import QdrantClient
from qdrant_client.http import models
from app.utils import extract_text_from_file, chunk_text

class RagModule:
    """Module for Retrieval-Augmented Generation using Qdrant and Ollama embeddings."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.qdrant_client = None
        self.available = False
        self.embedding_model = None
        self.collection_name_prefix = "user_"
        self.chunk_size = 500
        self.chunk_overlap = 50
        self.top_k = 5
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize module with Flask app configuration."""
        qdrant_url = app.config.get('QDRANT_URL')
        qdrant_api_key = app.config.get('QDRANT_API_KEY')
        self.embedding_model = app.config.get('EMBEDDING_MODEL', 'bge-m3:latest')
        self.chunk_size = app.config.get('RAG_CHUNK_SIZE', 500)
        self.chunk_overlap = app.config.get('RAG_CHUNK_OVERLAP', 50)
        self.top_k = app.config.get('RAG_TOP_K', 5)

        if not qdrant_url:
            app.logger.warning("QDRANT_URL not set, RAG module disabled")
            self.available = False
            return

        try:
            self.qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
            # Test connection
            self.qdrant_client.get_collections()
            self.available = True
            app.logger.info(f"RagModule initialized with Qdrant at {qdrant_url}")
        except Exception as e:
            self.available = False
            app.logger.error(f"Failed to connect to Qdrant: {e}")

    def _get_collection_name(self, user_id):
        """Return collection name for a specific user."""
        return f"{self.collection_name_prefix}{user_id}"

    def _ensure_collection(self, user_id):
        """Create collection for user if it doesn't exist."""
        collection_name = self._get_collection_name(user_id)
        try:
            self.qdrant_client.get_collection(collection_name)
        except Exception:
            # Collection doesn't exist, create it
            # Determine vector size by getting a test embedding
            test_emb = self._get_embedding("test")
            if test_emb is None:
                raise RuntimeError("Cannot get embedding to determine vector size")
            vector_size = len(test_emb)
            self.qdrant_client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE)
            )
            self.logger.info(f"Created collection {collection_name} with vector size {vector_size}")

    def index_document(self, user_id, doc_id, file_path):
        """
        Extract text from document, chunk it, generate embeddings and store in Qdrant.
        Returns (success, message).
        """
        if not self.available:
            return False, "RAG service unavailable"

        # 1. Extract text
        text = extract_text_from_file(file_path)
        if not text:
            return False, "Failed to extract text from document"

        # 2. Chunk text
        chunks = chunk_text(text, self.chunk_size, self.chunk_overlap)
        if not chunks:
            return False, "No text chunks generated"

        # 3. Get embeddings for each chunk
        embeddings = []
        for chunk in chunks:
            emb = self._get_embedding(chunk)
            if emb is None:
                return False, "Failed to get embedding for a chunk"
            embeddings.append(emb)

        # 4. Prepare points with valid UUIDs as IDs
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
                    "text": chunk
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
            return True, f"Indexed {len(chunks)} chunks"
        except Exception as e:
            self.logger.error(f"Error during upsert: {e}")
            return False, f"Qdrant error: {str(e)}"

    def delete_document(self, doc_id, user_id):
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
            return True
        except Exception as e:
            # If the collection does not exist, there is nothing to delete -> treat as success
            if "Not found" in str(e) or "doesn't exist" in str(e):
                return True
            self.logger.error(f"Failed to delete document {doc_id} from index: {e}")
            return False

    def search(self, user_id, query, top_k=None):
        """
        Search for relevant chunks based on query.
        Returns list of chunk texts.
        """
        if not self.available:
            return []

        top_k = top_k or self.top_k
        query_emb = self._get_embedding(query)
        if query_emb is None:
            return []

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
            chunks = [hit.payload["text"] for hit in search_result]
            return chunks
        except Exception as e:
            self.logger.error(f"Qdrant search error: {e}")
            return []

    def generate_answer(self, user_id, query, session_id, lang='ru'):
        """
        Full RAG answer: search + call reasoning model with context.
        Returns (answer, error_message).
        """
        chunks = self.search(user_id, query)
        if not chunks:
            return None, "No relevant documents found"

        context = "\n\n".join(chunks)
        reasoning_module = current_app.modules.get('base')
        if not reasoning_module:
            return None, "Reasoning module unavailable"

        # Construct prompt with context
        prompt = f"Answer the user's question using only the provided context.\n\nContext:\n{context}\n\nQuestion: {query}\n\nAnswer:"
        response = reasoning_module.call_ollama(
            [{'role': 'user', 'content': prompt}],
            model_type='reasoning',
            lang=lang
        )
        return response, None

    def _get_embedding(self, text):
        """Get embedding vector from Ollama."""
        ollama_url = current_app.config.get('OLLAMA_URL')
        try:
            response = requests.post(
                f"{ollama_url}/api/embeddings",
                json={"model": self.embedding_model, "prompt": text}
            )
            if response.status_code == 200:
                return response.json()["embedding"]
            else:
                self.logger.error(f"Ollama embedding error: {response.text}")
                return None
        except Exception as e:
            self.logger.error(f"Error getting embedding: {e}")
            return None