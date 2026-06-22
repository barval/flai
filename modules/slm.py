# modules/slm.py
"""
Long-term memory module via SuperLocalMemory (SLM).

Provides persistent, cross-session memory for AI conversations using
local-only zero-LLM retrieval (Fisher-Rao metric, SQLite-backed).
Each user gets their own isolated database via SLM_DATA_DIR.
"""

import logging
from typing import Any

import requests

from app.mixins import TranslationMixin


class SlmModule(TranslationMixin):
    """Interface to SuperLocalMemory for long-term cross-session memory."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.url = None
        self.available = False
        self.recall_limit = 3

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize with Flask app config."""
        self.url = app.config.get("SLM_URL", "http://flai-slm:8766").rstrip("/")
        self.recall_limit = app.config.get("SLM_RECALL_LIMIT", 3)
        self.check_availability()

    def check_availability(self) -> bool:
        """Check if SLM MCP server is reachable."""
        if not self.url:
            self.available = False
            return False
        try:
            resp = requests.get(f"{self.url}/health", timeout=5)
            self.available = resp.status_code == 200
            if self.available:
                self.logger.info(f"SuperLocalMemory available at {self.url}")
            else:
                self.logger.warning(f"SuperLocalMemory health failed: {resp.status_code}")
            return self.available  # type: ignore[no-any-return]
        except Exception as e:
            self.logger.warning(f"SuperLocalMemory not available: {e}")
            self.available = False
            return False

    def remember(self, text: str, metadata: dict[str, Any] | None = None, profile: str | None = None) -> bool:
        """Save a fact to long-term memory.

        Args:
            text: The fact string to store.
            metadata: Optional dict with session_id, type, etc.
            profile: User ID for per-user database isolation.

        Returns:
            True if saved successfully.
        """
        if not self.available and not self.check_availability():
            return False
        payload: dict[str, Any] = {"text": text}
        if metadata:
            payload["metadata"] = metadata
        if profile:
            payload["profile"] = profile
        try:
            resp = requests.post(f"{self.url}/remember", json=payload, timeout=300)
            return resp.status_code == 200
        except Exception as e:
            self.logger.warning(f"SLM remember failed: {e}")
            return False

    def recall(self, query: str, limit: int | None = None, profile: str | None = None, semantic: bool = False) -> list[dict[str, Any]]:
        """Retrieve relevant facts from long-term memory.

        Args:
            query: Search query string.
            limit: Max results to return.
            profile: User ID for per-user database isolation.
            semantic: If True, use full semantic search (subprocess, slower).
                      If False, read latest facts via direct SQLite (fast).

        Returns:
            List of dicts with 'text', 'score' keys.
        """
        if not self.available and not self.check_availability():
            return []
        limit = limit or self.recall_limit
        payload: dict[str, Any] = {"query": query, "limit": limit, "semantic": semantic}
        if profile:
            payload["profile"] = profile
        try:
            resp = requests.post(
                f"{self.url}/recall",
                json=payload,
                timeout=15 if not semantic else 30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("results", [])  # type: ignore[no-any-return]
            return []
        except Exception as e:
            self.logger.warning(f"SLM recall failed: {e}")
            return []

    def forget(self, query: str, profile: str | None = None) -> bool:
        """Delete memories matching a query (fuzzy delete).

        Args:
            query: Query to match memories for deletion.
            profile: User ID for per-user database isolation.

        Returns:
            True if forgotten successfully.
        """
        if not self.available:
            return False
        payload: dict[str, Any] = {"query": query}
        if profile:
            payload["profile"] = profile
        try:
            resp = requests.post(f"{self.url}/forget", json=payload, timeout=30)
            return resp.status_code == 200
        except Exception as e:
            self.logger.warning(f"SLM forget failed: {e}")
            return False

    def list_facts(self, limit: int = 20, profile: str | None = None) -> list[dict[str, Any]]:
        """List recent memories chronologically.

        Args:
            limit: Max number of facts to return.
            profile: User ID for per-user database isolation.

        Returns:
            List of dicts with fact details.
        """
        if not self.available:
            return []
        payload: dict[str, Any] = {"limit": limit}
        if profile:
            payload["profile"] = profile
        try:
            resp = requests.post(f"{self.url}/list", json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", {}).get("results", [])  # type: ignore[no-any-return]
            return []
        except Exception as e:
            self.logger.warning(f"SLM list failed: {e}")
            return []

    def delete_fact(self, fact_id: str, profile: str | None = None) -> bool:
        """Delete a specific fact by ID.

        Args:
            fact_id: The ID of the fact to delete.
            profile: User ID for per-user database isolation.

        Returns:
            True if deleted successfully.
        """
        if not self.available:
            return False
        payload: dict[str, Any] = {"id": fact_id}
        if profile:
            payload["profile"] = profile
        try:
            resp = requests.post(f"{self.url}/delete", json=payload, timeout=30)
            return resp.status_code == 200
        except Exception as e:
            self.logger.warning(f"SLM delete_fact failed: {e}")
            return False

    def check_similarity(self, text: str, profile: str | None = None) -> float:
        """Check semantic similarity of text against existing facts.

        Uses the SLM daemon's embedding model to find the closest fact
        and returns its similarity score (0.0–1.0).

        Args:
            text: Candidate text to check.
            profile: User ID for per-user database isolation.

        Returns:
            Similarity score (0.0 = no match, 1.0 = identical).
        """
        if not self.available and not self.check_availability():
            return 0.0
        payload: dict[str, Any] = {"text": text}
        if profile:
            payload["profile"] = profile
        try:
            resp = requests.post(f"{self.url}/similarity", json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("max_similarity", 0.0)  # type: ignore[no-any-return]
            return 0.0
        except Exception as e:
            self.logger.warning(f"SLM check_similarity failed: {e}")
            return 0.0


