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

    def get_context(self, query: str, lang: str = "ru", limit: int | None = None, profile: str | None = None, semantic: bool = False) -> str:
        """Get formatted context string for prompt enrichment.

        Returns a multi-line string with relevant facts from long-term memory,
        or an empty string if SLM is unavailable or no facts found.

        Args:
            query: The user's current query.
            lang: Language code for the header text.
            limit: Max facts to include.
            profile: User ID for per-user database isolation.
            semantic: If True, use full semantic search (slower but more relevant).

        Returns:
            Formatted context string ready for injection into a prompt.
        """
        facts = self.recall(query, limit=limit, profile=profile, semantic=semantic)
        if not facts:
            return ""

        header = "Relevant context from long-term memory:" if lang == "en" else "Контекст из долговременной памяти:"
        lines = [header]
        for f in facts:
            lines.append(f"- {f.get('content', f.get('text', ''))}")
        return "\n".join(lines)
