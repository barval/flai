# app/queue.py
import contextlib
import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import redis
from flask_babel import force_locale

from .db import (
    INDEX_STATUS_FAILED,
    INDEX_STATUS_INDEXED,
    INDEX_STATUS_INDEXING,
    INDEX_STATUS_PENDING,
    get_current_time_for_db,
    save_message,
    update_document_index_status,
)
from .events import get_events_publisher
from .model_config import get_model_config
from .tools import MAX_TOOL_ITERATIONS, execute_tool, get_tool_definitions
from .utils import (
    _load_skills_section,
    estimate_tokens,
    get_current_time_in_timezone,
    get_current_time_in_timezone_for_db,
    save_uploaded_file,
)


def _parse_remember_json(llm_response: str) -> list[str]:
    """Parse remember response JSON: {"confirmed": true, "facts": [...]}"""
    try:
        # Extract JSON from response
        start = llm_response.find("{")
        end = llm_response.rfind("}") + 1
        if start == -1 or end <= start:
            return []
        data = json.loads(llm_response[start:end])
        if data.get("confirmed"):
            return data.get("facts", [])
        return []
    except Exception:
        return []


def _extract_facts_bg(app, query: str, response: str, session_id: str, user_id: str, lang: str) -> None:
    """Extract facts from Q&A in a background thread (CPU-only, no GPU lock).

    Called by daemon threads after reasoning/chat responses. Catches all
    exceptions to prevent silent thread crashes.
    """
    try:
        slm = app.modules.get("slm")
        if not slm or not slm.available:
            return

        from app.slm_extract import extract_facts_from_exchange

        existing = slm.list_facts(limit=50, profile=user_id)
        facts = extract_facts_from_exchange(query, response, existing, lang=lang)

        for fact in facts:
            try:
                similarity = slm.check_similarity(fact["text"], profile=user_id)
                if similarity >= 0.85:
                    continue
            except Exception:
                pass

            slm.remember(
                fact["text"],
                metadata={
                    "session_id": session_id,
                    "fact_type": fact.get("fact_type", "general"),
                    "category": fact.get("category", "context"),
                    "source": "extraction",
                },
                profile=user_id,
            )

        if facts:
            app.logger.info(f"Background fact extraction: {len(facts)} facts for {user_id}")

    except Exception as e:
        app.logger.warning(f"Background fact extraction failed: {e}")


class RedisRequestQueue:
    """Redis-based request queue with JSON serialization for security."""

    def __init__(self, app):
        self.app = app
        self.logger = app.logger
        self.redis = redis.from_url(
            app.config["REDIS_URL"],
            decode_responses=True,
            socket_timeout=30,
            socket_connect_timeout=3,
            retry_on_timeout=True,
        )
        self.queue_key = "request_queue"
        self.slow_queue_key = "slow_request_queue"
        self.background_queue_key = "background_queue"
        self.processing_key = "processing_requests"
        self.slow_processing_key = "slow_processing_requests"
        self.background_processing_key = "background_processing"
        self.results_key = "request_results"
        self.user_requests_key = "user_requests"
        # HMAC key for signing serialized data (prevent tampering)
        self.hmac_key = app.config.get("SECRET_KEY", "fallback-key").encode("utf-8")

        # NEW: Serialize ALL GPU-heavy operations globally to prevent OOM
        self._gpu_lock = threading.Lock()
        self._video_unload_lock = threading.Lock()

        self.start_worker()

    def _serialize(self, data: dict) -> str:
        """Serialize data to JSON with HMAC signature."""
        json_str = json.dumps(data, ensure_ascii=False)
        signature = hmac.new(self.hmac_key, json_str.encode("utf-8"), hashlib.sha256).hexdigest()
        return json.dumps({"data": json_str, "sig": signature}, ensure_ascii=False)

    def _deserialize(self, signed_json: str) -> dict | None:
        """Deserialize JSON with HMAC verification."""
        try:
            wrapper = json.loads(signed_json)
            json_str = wrapper["data"]
            stored_sig = wrapper["sig"]
            # Verify signature
            expected_sig = hmac.new(self.hmac_key, json_str.encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(stored_sig, expected_sig):
                self.logger.error("HMAC signature mismatch - possible tampering")
                return None
            return json.loads(json_str)  # type: ignore[no-any-return]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            self.logger.error(f"Failed to deserialize data: {e}")
            return None

    def start_worker(self):
        """Start worker threads for fast and slow task queues."""
        self.app.logger.info("RedisRequestQueue: starting fast and slow workers")
        # Shutdown event for graceful termination
        self._shutdown_event = threading.Event()

        # NEW: Global GPU serialization locks
        if not hasattr(self, "_gpu_lock"):
            self._gpu_lock = threading.Lock()
        if not hasattr(self, "_video_unload_lock"):
            self._video_unload_lock = threading.Lock()
        # Fast worker — text, audio, RAG, camera
        fast_thread = threading.Thread(target=self._worker_loop_fast, name="fast-worker", daemon=False)
        fast_thread.start()
        self._fast_worker_thread = fast_thread
        # Slow worker — image generation/editing
        slow_thread = threading.Thread(target=self._worker_loop_slow, name="slow-worker", daemon=False)
        slow_thread.start()
        self._slow_worker_thread = slow_thread
        self.app.logger.info("RedisRequestQueue: workers started (fast + slow)")

    def stop_workers(self, timeout=30):
        """Signal workers to stop and wait for them to finish."""
        self.app.logger.info("RedisRequestQueue: signaling workers to stop")
        self._shutdown_event.set()
        if hasattr(self, "_fast_worker_thread"):
            self._fast_worker_thread.join(timeout=timeout)
        if hasattr(self, "_slow_worker_thread"):
            self._slow_worker_thread.join(timeout=timeout)
        self.app.logger.info("RedisRequestQueue: workers stopped")

    def _classify_task(self, task: dict[str, Any]) -> str:
        """Classify task as 'fast' or 'slow' for queue routing."""
        # Check top-level type first (for reindex_all from add_reindex_all_task)
        task_type = task.get("type", "")
        if task_type in ("index_document", "reindex_all_embeddings"):
            return "slow"  # Indexing can be slow
        if task_type == "fact_merge_task":
            return "background"  # Background task — runs only in idle, no GPU lock
        if task_type == "fact_extraction_task":
            return "slow"  # LLM-based extraction is slow
        # Also check type inside data (for index_document from documents.py)
        request_data = task.get("data", {})
        req_type = request_data.get("type", "text")
        if req_type in ("index_document", "reindex_all_embeddings"):
            return "slow"  # Indexing can be slow
        request_data = task.get("data", {})
        req_type = request_data.get("type", "text")
        file_type = request_data.get("file_type", "")
        message_text = request_data.get("text", "")

        # Audio is fast
        if file_type and file_type.startswith("audio/"):
            return "fast"
        # Image with text comment — could be editing (slow) or analysis (fast)
        # We'll classify as slow initially — the multimodal model will decide later
        if req_type == "image" and file_type and file_type.startswith("image/"):
            if message_text and message_text.strip():
                return "slow"
            return "fast"
        # Image generation (re-queued from router) is slow
        if req_type == "image_gen":
            return "slow"
        if req_type == "reasoning_task":
            return "slow"
        # Text tasks are fast
        if req_type == "text":
            return "fast"
        # Video tasks are slow
        if req_type == "video":
            return "slow"
        # Transcription is medium — use fast queue
        if task_type == "transcribe_audio":
            return "fast"
        # Default to slow for safety
        return "slow"

    def add_request(
        self, user_id: str, session_id: str, request_data: dict[str, Any], user_class: int, lang: str = "ru"
    ) -> tuple[str, dict[str, Any]]:
        """Add a request to the appropriate queue (fast or slow)."""
        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "user_id": user_id,
            "session_id": session_id,
            "data": request_data,
            "user_class": user_class,
            "lang": lang,
            "timestamp": time.time(),
        }

        # Classify and route to appropriate queue
        queue_type = self._classify_task(task)
        if queue_type == "background":
            queue_key = self.background_queue_key
        elif queue_type == "slow":
            queue_key = self.slow_queue_key
        else:
            queue_key = self.queue_key

        serialized = self._serialize(task)

        # Atomic: RPUSH + HINCRBY in the same pipeline so get_user_queue_counts()
        # never sees a task in the LIST before the hash counter is updated.
        user_count_key = f"{self.queue_key}:user_counts"
        pipe = self.redis.pipeline()
        pipe.rpush(queue_key, serialized)
        pipe.hincrby(user_count_key, user_id, 1)
        pipe.hincrby(user_count_key, "__total__", 1)
        pipe.execute()

        # Get position in queue
        position = self.redis.llen(queue_key)

        return task_id, {
            "position": position,
            "estimated_seconds": self._estimate_wait(queue_type, position),
            "queue_type": queue_type,
        }

    def add_reindex_all_task(self, lang: str = "ru") -> str:
        """Add a reindex-all task to the slow queue."""
        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "user_id": "system",
            "session_id": "system",
            "type": "reindex_all_embeddings",
            "data": {},
            "user_class": 0,  # Highest priority
            "lang": lang,
            "timestamp": time.time(),
        }
        serialized = self._serialize(task)
        self.redis.rpush(self.slow_queue_key, serialized)
        self.app.logger.info(f"Reindex task added: {task_id}")
        return task_id

    def _estimate_wait(self, queue_type: str, position: int) -> int:
        """Estimate wait time in seconds based on queue type and position."""
        if queue_type == "slow":
            return position * 300
        else:
            return position * 3

    def get_user_queue_counts(self, user_id: str) -> tuple[int, int]:
        """Get user's queue count and total queue+processing length."""
        fast_total = self.redis.llen(self.queue_key)
        slow_total = self.redis.llen(self.slow_queue_key)
        fast_proc = self.redis.hlen(self.processing_key)
        slow_proc = self.redis.hlen(self.slow_processing_key)
        total = fast_total + slow_total + fast_proc + slow_proc
        if total == 0:
            return 0, 0

        user_count_key = f"{self.queue_key}:user_counts"
        user_count = self.redis.hget(user_count_key, user_id)
        user_count = int(user_count) if user_count else 0

        # Cap user_count to total — prevents impossible displays like "2/1"
        # and negative values from background task counter drift.
        return max(0, min(user_count, total)), total

    def _decrement_user_queue_count(self, user_id: str):
        """Decrement user's queue count (O(1))."""
        user_count_key = f"{self.queue_key}:user_counts"
        pipe = self.redis.pipeline()
        pipe.hincrby(user_count_key, user_id, -1)
        pipe.hincrby(user_count_key, "__total__", -1)
        pipe.execute()

    def _cleanup_user_request(self, user_id: str, request_id: str):
        """Remove request ID from user's set after completion."""
        self.redis.srem(f"{self.user_requests_key}:{user_id}", request_id)

    def _get_model_for_task(self, task: dict[str, Any]) -> str:
        """Determine which llama.cpp model a task will need."""
        task_type = task.get("type", "")
        data = task.get("data", {})
        req_type = data.get("type", "")
        file_type = data.get("file_type", "")
        action_type = data.get("action_type", "")

        if task_type in ("index_document", "reindex_all_embeddings"):
            return "none"
        if task_type == "transcribe_audio":
            return "none"

        if action_type == "rag":
            return "reasoning"

        if action_type == "search":
            return "none"

        if file_type and file_type.startswith("audio/"):
            return "chat"

        if req_type == "image" and file_type and file_type.startswith("image/"):
            return "multimodal"
        if req_type == "image_gen":
            return "multimodal"
        if req_type == "reasoning_task":
            return "reasoning"

        if req_type == "text":
            return "chat"

        return "chat"

    def _cleanup_vram_after_task(self, task: dict[str, Any]) -> None:
        """Free VRAM after a GPU-using task completes.

        Only unloads LTX-Video pipeline and restarts its container for video tasks.
        Non-chat llama.cpp models (reasoning, multimodal, embedding) are NOT unloaded
        here — their TTL=0 makes llama-swap unload them automatically after the response.
        Chat model is NEVER unloaded here — it stays hot permanently (TTL=1 year).
        """
        try:
            from app.resource_manager import get_resource_manager

            rm = get_resource_manager()
            req_type = task.get("data", {}).get("type", "")
            if req_type == "video":
                rm.unload_video_pipeline()
                # Restart LTX-Video container to free CUDA context (~3 GB).
                # The gunicorn worker inside flai-ltxvideo holds a CUDA context that survives
                # /v1/unload — only a container restart releases it.
                rm._force_restart_ltx_video()
            # Invalidate active model tracking in ALL llamacpp instances
            for module_name in ("base", "multimodal", "rag"):
                module = self.app.modules.get(module_name)
                if module and hasattr(module, "llamacpp"):
                    module.llamacpp.reset_active_model()
            # Preload chat model in background so the next router call is instant
            self._preload_chat_model_background()
        except Exception as e:
            self.logger.debug(f"VRAM cleanup after task: {e}")

    def _preload_chat_model_background(self) -> None:
        """Trigger chat model loading in llama-swap via a background thread.

        After a non-chat model finishes, llama-swap unloads it (TTL=0) and
        the chat model is no longer in VRAM. This sends a tiny chat completion
        request in a daemon thread to trigger llama-swap to load the chat model,
        so the next router call doesn't suffer a cold start (~20-30s).
        """
        try:
            import requests as req

            from app.model_config import get_model_config

            swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080").rstrip("/")

            # Skip if chat model is already loaded
            try:
                resp = req.get(f"{swap_url}/running", timeout=2)
                if resp.status_code == 200:
                    running = resp.json().get("running", [])
                    config = get_model_config("chat")
                    model_name = config.get("model_name", "") if config else ""
                    if model_name and any(model_name in m.get("cmd", "") for m in running):
                        self.logger.debug("Chat model already loaded, skipping preload")
                        return
            except Exception:
                pass

            def _do_preload():
                try:
                    resp = req.post(
                        f"{swap_url}/v1/chat/completions",
                        json={
                            "model": "chat",
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        },
                        timeout=60,
                    )
                    if resp.status_code == 200:
                        self.logger.info("Chat model preloaded in background after non-chat task")
                    else:
                        self.logger.debug(f"Chat preload returned {resp.status_code}")
                except Exception as e:
                    self.logger.debug(f"Background chat preload failed: {e}")

            thread = threading.Thread(target=_do_preload, daemon=True)
            thread.start()
        except Exception as e:
            self.logger.debug(f"Chat preload setup failed: {e}")

    def _process_single_task(self, task: dict[str, Any], processing_key: str) -> None:
        """Process a single task: move to processing, execute, store result, cleanup."""
        task_id = task.get("id")
        if not task_id:
            return

        processing_ttl = self.app.config.get("QUEUE_MAX_WAIT_TIME", 300) + 60
        self.redis.hset(processing_key, task_id, self._serialize({**task, "moved_at": time.time()}))
        self.redis.expire(processing_key, processing_ttl)

        queue_time = time.time() - task.get("timestamp", time.time())
        max_wait_time = self.app.config.get("QUEUE_MAX_WAIT_TIME", 300)
        if queue_time > max_wait_time:
            self.app.logger.warning(f"Task {task_id} waited too long in queue ({queue_time:.1f}s). Cancelling.")
            template = self.app.modules["base"]._(
                "Request cancelled - too long in queue ({queue_time:.1f}s)", lang=task.get("lang", "ru")
            )
            error_text = template.format(queue_time=queue_time)
            result_ttl = self.app.config.get("REDIS_RESULT_TTL", 3600)
            self.redis.hset(
                self.results_key,
                task_id,
                self._serialize(
                    {
                        "status": "error",
                        "error": error_text,
                        "result": {"session_id": task.get("session_id")},
                        "timestamp": time.time(),
                    }
                ),
            )
            self.redis.expire(self.results_key, result_ttl)
            self.redis.hdel(processing_key, task_id)
            user_id = task.get("user_id")
            if user_id:
                self._cleanup_user_request(user_id, task_id)
                self._decrement_user_queue_count(user_id)
            self._publish_result_event(task, "error", {"error": error_text, "session_id": task.get("session_id")})
            return

        final_result = None
        try:
            with self.app.app_context():
                result_data = self._process_request(task)
                final_result = result_data
                if "session_id" not in result_data and task.get("session_id"):
                    result_data["session_id"] = task.get("session_id")
                self.redis.hset(
                    self.results_key,
                    task_id,
                    self._serialize({"status": "completed", "result": result_data, "timestamp": time.time()}),
                )
                self.redis.expire(self.results_key, self.app.config.get("REDIS_RESULT_TTL", 3600))
                self.app.logger.info(f"Task {task_id} completed successfully for session {task.get('session_id')}")
                self._publish_result_event(task, "completed", result_data)
        except Exception as e:
            self.app.logger.error(f"Error processing task {task_id}: {e}", exc_info=True)
            self.redis.hset(
                self.results_key,
                task_id,
                self._serialize(
                    {
                        "status": "error",
                        "error": str(e),
                        "result": {"session_id": task.get("session_id")},
                        "timestamp": time.time(),
                    }
                ),
            )
            self.redis.expire(self.results_key, self.app.config.get("REDIS_RESULT_TTL", 3600))
            self._publish_result_event(task, "error", {"error": str(e), "session_id": task.get("session_id")})
        finally:
            self.redis.hdel(processing_key, task_id)
            user_id = task.get("user_id")
            if user_id:
                self._cleanup_user_request(user_id, task_id)
                # Don't decrement for background tasks — they were never
                # incremented via add_request(), so decrementing causes
                # the user counter to drift negative.
                if task.get("type") not in self._BACKGROUND_TASK_TYPES:
                    self._decrement_user_queue_count(user_id)

        # Skip VRAM cleanup if task was requeued — next worker needs current GPU state.
        # E.g.: image_chat → [-VIDEO-] → requeue → slow worker uses same multimodal model.
        if final_result is not None and final_result.get("status") == "queued":
            self.logger.debug(f"Skipping VRAM cleanup: task {task_id} requeued (status=queued)")
        else:
            current_model = self._get_model_for_task(task)
            # Chat model stays hot in VRAM (TTL=1 year) — only cleanup non-chat models.
            req_type = task.get("data", {}).get("type", "")
            if current_model in ("reasoning", "multimodal", "embedding") or req_type == "video":
                self._cleanup_vram_after_task(task)

    def _worker_loop_fast(self):
        """Worker for fast queue (text, audio, RAG, camera, image chat)."""
        self.app.logger.info("Fast worker started")
        while not self._shutdown_event.is_set():
            try:
                result = self.redis.blpop(self.queue_key, timeout=5)
                if not result:
                    continue
                _, task_data = result
                task = self._deserialize(task_data)
                if task is None:
                    self.logger.error("Fast worker: failed to deserialize task")
                    continue

                # GPU tasks must serialize with slow worker
                model = self._get_model_for_task(task)
                if model in ("chat", "multimodal", "reasoning", "embedding"):
                    with self._gpu_lock:
                        self._process_single_task(task, self.processing_key)
                else:
                    self._process_single_task(task, self.processing_key)
            except Exception as e:
                self.logger.error(f"Fast worker error: {e}")
                time.sleep(1)
        self.app.logger.info("Fast worker stopped gracefully")

    def _worker_loop_slow(self):
        """Worker for slow queue (image/video generation, editing)."""
        self.app.logger.info("Slow worker started")
        while not self._shutdown_event.is_set():
            try:
                # Priority 1: slow queue (user-facing GPU tasks)
                result = self.redis.blpop(self.slow_queue_key, timeout=1)
                if result:
                    _, task_data = result
                    task = self._deserialize(task_data)
                    if task is None:
                        self.logger.error("Slow worker: failed to deserialize task")
                        continue
                    with self._gpu_lock:
                        self._process_single_task(task, self.slow_processing_key)
                    continue

                # Priority 2: background queue (only when fast queue is empty)
                if (self.redis.llen(self.background_queue_key) > 0
                        and self.redis.llen(self.queue_key) == 0):
                    result = self.redis.blpop(self.background_queue_key, timeout=5)
                    if result:
                        _, task_data = result
                        task = self._deserialize(task_data)
                        if task is None:
                            self.logger.error("Slow worker: failed to deserialize background task")
                            continue
                        # Background tasks (CPU merge) — no GPU lock needed
                        self._process_single_task(task, self.background_processing_key)
                        continue

            except Exception as e:
                self.logger.error(f"Slow worker error: {e}")
                time.sleep(1)
        self.app.logger.info("Slow worker stopped gracefully")

    def _get_model_name(self, module_type: str) -> str | None:
        config = get_model_config(module_type)
        return config.get("model_name") if config else None

    def _try_rag_answer(
        self,
        query: str,
        session_id: str,
        user_id: str,
        lang: str,
        strict: bool = False,
        response_style: str = "neutral",
        token_callback: Callable[[str], None] | None = None,
    ) -> tuple[str | None, str | None]:
        """Attempt to answer using RAG."""
        rag = self.app.modules.get("rag")
        if rag and rag.available:
            if strict:
                threshold = self.app.config.get("RAG_RELEVANCE_THRESHOLD_REASONING", 0.5)
            else:
                threshold = self.app.config.get("RAG_RELEVANCE_THRESHOLD_DEFAULT", 0.3)
            answer, error, model_name = rag.generate_answer(
                user_id,
                query,
                session_id,
                lang=lang,
                threshold=threshold,
                response_style=response_style,
                token_callback=token_callback,
            )
            if answer is not None and error is None:
                return answer, model_name
            if error:
                self.logger.warning(
                    f"RAG generate_answer returned error: {error} for query: {query[:80]}... user_id={user_id}"
                )
        return None, None

    def _build_error_response(self, session_id: str, error: str, process_time: float, lang: str) -> dict[str, Any]:
        """Build a standardized error response dict and save to DB.

        response_time is stored in DB (for analytics) but intentionally omitted
        from the returned dict so the client does not render ⏱️/🚀/🤖 in the
        error message header.
        """
        from .db import save_message

        completion_time = get_current_time_in_timezone_for_db(self.app)
        prefix = "" if error.startswith("⚠️") else "⚠️ "
        msg_id = save_message(
            session_id, "assistant", prefix + error, model_name="system", response_time=str(process_time)
        )
        return {
            "error": error,
            "session_id": session_id,
            "assistant_timestamp": completion_time,
            "is_error": True,
            "message_id": msg_id,
            "model_used": "system",
            "model_type": "system",
        }

    @staticmethod
    def _is_llm_error_string(text: str) -> bool:
        """Check if a string from call_llamacpp/chat_stream is an error message.

        Uses prefix matching (not substring) to avoid false positives when the
        LLM generates code containing words like "Error:", "Timeout", "GPU memory".
        Backend errors always START with these phrases — never appear at position 0
        in LLM-generated code or explanations.

        Also handles streaming: if the accumulated text contains "⚠️ " followed
        by an error indicator, it's an error even if it doesn't start the string.
        The "⚠️ " marker is only added by _format_user_error(), never by the LLM.
        """
        if not isinstance(text, str):
            return False
        # Level 1: "⚠️ " prefix catches most errors from _format_user_error()
        if text.startswith("⚠️"):
            return True
        # Level 2: Specific prefix patterns for errors returned as plain strings
        # from call_llamacpp(), chat_stream(), process_reasoning(), and queue.py _().
        # Both English and Russian variants are listed.
        error_prefixes = (
            # GPU memory errors (en + ru)
            "GPU memory ",  # "GPU memory unavailable..."
            "Память GPU ",  # ru: "Память GPU недоступна..."
            "Проверка памяти GPU",  # ru: "Проверка памяти GPU не удалась"
            # Timeout (en + ru)
            "Timeout (",  # "Timeout (300s) when calling..."
            "Таймаут (",  # ru: "Таймаут (300с) при вызове..."
            # Connection errors (en + ru)
            "Could not connect",  # "Could not connect to llama-server/swap"
            "Не удалось подключиться",  # ru
            # Service availability (en + ru)
            "Service temporarily unavailable",  # circuit breaker
            "Сервис временно недоступен",  # ru
            # Model configuration (en + ru)
            "Model configuration missing",
            "Model for ",  # "Model for chat not configured"
            "Модель для ",  # ru
            "не настроена",  # ru: "Модель для chat не настроена"
            # Generic errors (en + ru)
            "Error: ",  # "Error: <exception>" (en, note trailing space)
            "Ошибка: ",  # ru: "Ошибка: <exception>" (note trailing space)
            "Internal error:",  # "Internal error: no response from model"
            "Внутренняя ошибка:",  # ru
            "HTTP error",  # "HTTP error 400" (en)
            "Ошибка HTTP",  # ru
            # Request errors (en + ru)
            "Request too long",  # "Request too long, please simplify"
            "Запрос слишком длинный",  # ru
            "Request cancelled",  # "Request cancelled - too long in queue"
            "Запрос отменён",  # ru
            # Model response errors (en + ru)
            "Model returned empty response",
            "Модель вернула пустой ответ",  # ru
            # Reasoning errors (en + ru)
            "Reasoning model unavailable:",  # "Reasoning model unavailable: GPU memory..."
            "Модель рассуждения недоступна:",  # ru
            "Reasoning failed:",  # "Reasoning failed: GPU memory exhausted..."
            "Рассуждение не удалось:",  # ru
            # Video generation errors (en + ru)
            "Video generation failed:",  # "Video generation failed: GPU memory..."
            "Генерация видео не удалась:",  # ru
            # Prompt template errors (en + ru)
            "Error loading prompt template",
            "Ошибка загрузки шаблона промпта",  # ru
            # CUDA OOM (from llama.cpp directly, not translated)
            "CUDA out of memory",
            # llama-swap specific (Go binary, not translated)
            "upstream command exited",
            "exited prematurely",
            "unable to start process",
        )
        if any(text.startswith(pfx) for pfx in error_prefixes):
            return True
        # Level 3: Streaming errors — "⚠️ " marker appears after normal tokens.
        # The "⚠️ " prefix is only added by _format_user_error(), never by LLM.
        # So if we find "⚠️ " followed by an error prefix, it's definitely an error.
        if "⚠️ " in text:
            idx = text.index("⚠️ ")
            tail = text[idx + 3 :]  # skip "⚠️ "
            if any(tail.startswith(pfx) for pfx in error_prefixes):
                return True
        return False

    @staticmethod
    def _strip_thinking_tags(text: str) -> str:
        """Remove thinking/reasoning blocks from model output.

        Primary filtering happens in llamacpp_client.py at the backend level.
        This is a safety net for responses saved to DB.
        Handles:
        - <think>...</think> blocks
        - <|channel|>analysis<|message|>...<|end|> — reasoning, stripped entirely
        - <|channel|>commentary<|message|>...ANSWER...<|end|> — unwrapped (answer kept)
        - Malformed <|channel|>... (no <|message|>) — stripped
        """
        if not text or ("<think" not in text and "<|channel|>" not in text):
            return text
        text = re.sub(r"<think[\s>][\s\S]*?</think>", "", text)
        text = re.sub(r"<\|channel\|>analysis<\|message\|>(?:[\s\S]*?<\|end\|>)?", "", text)
        text = re.sub(r"<\|channel\|>commentary<\|message\|>([\s\S]*?)<\|end\|>", r"\1", text)
        text = re.sub(r"<\|channel\|>[^<]*$", "", text)
        return text.strip()

    def _build_success_response(
        self,
        session_id: str,
        response: str,
        model_used: str,
        process_time: float | dict[str, float],
        message_id=None,
        extra: dict | None = None,
    ) -> dict[str, Any]:
        """Build a standardized success response dict."""
        result = {
            "response": response,
            "session_id": session_id,
            "model_used": model_used,
            "assistant_timestamp": get_current_time_in_timezone_for_db(self.app),
            "response_time": process_time,
            "is_error": False,
        }
        if message_id is not None:
            result["message_id"] = message_id
        if extra:
            result.update(extra)
        return result

    def _unload_video_pipeline(self) -> None:
        """Unload LTX-Video pipeline to free VRAM after generation."""
        try:
            from app.resource_manager import get_resource_manager

            rm = get_resource_manager()
            rm.unload_video_pipeline()
        except Exception:
            pass

    def _get_vram_needed(self, model_type: str) -> int:
        """Dynamic VRAM threshold for a model type (weights + KV cache + overhead)."""
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        return rm.get_vram_needed_mb(model_type)

    def _save_and_respond(
        self,
        session_id: str,
        text: str,
        model_name: str,
        process_time: float | dict[str, float],
        is_error: bool = False,
        file_data=None,
        file_type=None,
        file_name=None,
        file_path=None,
        extra: dict | None = None,
        response_style: str = "neutral",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Save assistant message to DB and return response dict.

        For error replies (is_error=True) the response_style and
        completion_tokens fields are intentionally omitted from the result
        dict so the client does not render 🚀/🤖/⏱️ in the message header.
        """
        resp_time = process_time if isinstance(process_time, dict) else str(process_time)
        completion_tokens = estimate_tokens(text) if text else 0
        msg_id = save_message(
            session_id,
            "assistant",
            text,
            file_data=file_data,
            file_type=file_type,
            file_name=file_name,
            file_path=file_path,
            model_name=model_name,
            response_time=resp_time,
            response_style=response_style,
            user_id=user_id,
            completion_tokens=completion_tokens,
        )
        result = self._build_success_response(
            session_id, text, model_name, process_time, message_id=msg_id, extra=extra
        )
        if is_error:
            # Strip style/tokens/response_time so the client header shows only
            # the model name (e.g. "system") — no ⏱️/🚀/🤖 decorations.
            result.pop("response_style", None)
            result.pop("completion_tokens", None)
            result.pop("response_time", None)
        else:
            result["response_style"] = response_style
            result["completion_tokens"] = completion_tokens
        return result

    # ── VRAM guard & GPU management ──────────────────────────────────────

    def _log_gpu_state_before_op(self, op_name: str, needed_mb: int):
        """Log current GPU state before an operation for debugging."""
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        rm.log_gpu_memory(f"{op_name}-pre")

        try:
            import requests as req

            swap_url = self.app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
            resp = req.get(f"{swap_url.rstrip('/')}/running", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                loaded_models = len(data.get("running", []))
                self.logger.info(
                    f"[{op_name}] Before: VRAM={rm.hardware.available_vram_mb}MB, "
                    f"loaded_llama_models={loaded_models}, need={needed_mb}MB"
                )
        except Exception as e:
            self.logger.debug(f"Failed to check llama-swap: {e}")

    def _check_vram_ready(self, needed_mb: int) -> bool:
        """Verify that sufficient free VRAM is available by polling nvidia-smi again."""
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0:
                free = int(out.stdout.strip().split("\n")[0].strip())
                ready = free >= needed_mb
                if not ready:
                    self.logger.warning(f"VRAM check failed after unload: {free}MB free, need {needed_mb}MB")
                return ready
        except Exception as e:
            self.logger.error(f"VRAM verification failed: {e}")
        return False

    def _wait_for_vram_full(self, timeout: int = 60) -> bool:
        """Wait until ALL LLM models are unloaded AND sufficient GPU VRAM is free.

        Unlike _wait_for_vram() which only checks a fixed MB threshold,
        this verifies via llama-swap /running endpoint AND requires
        enough VRAM for the video pipeline (estimated from file sizes
        or measured peak) — preventing OOM when video tries to load.

        Active unload synchronization: if models remain loaded in
        llama-swap after the initial POST /unload, re-triggers unload
        every poll cycle until /running reports 0.
        """
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        llamacpp_url = self.app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
        min_free = rm.estimate_video_vram_needed()
        deadline = time.time() + timeout

        self._unload_llamacpp_models()

        while time.time() < deadline:
            rm._poll_vram()
            free = rm.hardware.available_vram_mb

            # Check llama-swap /running for loaded models
            loaded_count = -1  # unknown
            try:
                import requests as req

                resp = req.get(f"{llamacpp_url.rstrip('/')}/running", timeout=5)
                if resp.status_code == 200:
                    loaded_count = len(resp.json().get("running", []))
            except Exception:
                pass

            if loaded_count == 0 and free >= min_free:
                self.logger.info(f"VRAM full-ready: {free}MB free, 0 LLM models loaded, need ≥{min_free}MB")
                return True

            if loaded_count == 0:
                self.logger.info(f"VRAM: {free}MB free, need {min_free}MB — waiting for deallocation...")
            else:
                self.logger.info(f"VRAM: {free}MB free, {loaded_count} model(s) loaded — re-triggering unload...")
                self._unload_llamacpp_models()
            time.sleep(1)

        self.logger.warning(f"VRAM wait timeout ({timeout}s): {free}MB free, need {min_free}MB, models={loaded_count}")
        return False

    def _unload_llamacpp_models(self):
        """Unload all llama.cpp models via llama-swap proxy.

        Must be called BEFORE any GPU-heavy operation (video, image gen, reasoning)
        to ensure no LLMs block VRAM availability.
        """
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        success = rm.unload_llamacpp_model()

        # Invalidate active model tracking in ALL llamacpp instances —
        # models were unloaded externally and _ensure_vram skip would be stale.
        for module_name in ("base", "multimodal", "rag"):
            module = self.app.modules.get(module_name)
            if module and hasattr(module, "llamacpp"):
                module.llamacpp.reset_active_model()

        if not success:
            self.logger.warning("Failed to unload llama.cpp models, proceeding anyway")

        return success

    def _wait_for_vram(self, needed_mb: int = 6000, timeout: int = 30) -> bool:
        """Block until at least ``needed_mb`` MB of VRAM is free AND no LLM tasks are running.

        First unloads llama.cpp models, then polls VRAM and llama-swap /running endpoint
        until sufficient space available AND no LLM processes occupy memory.
        Returns True on success, False if still insufficient after timeout.
        """
        self._unload_llamacpp_models()
        deadline = time.time() + timeout
        swap_url = self.app.config.get("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")

        while time.time() < deadline:
            try:
                import requests as req

                resp = req.get(f"{swap_url.rstrip('/')}/running", timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    running = data.get("running", [])
                    if len(running) > 0:
                        # Models reloaded by llama-swap (TTL) — unload again (no limit)
                        self.logger.info(f"VRAM: {len(running)} model(s) still running during wait, unloading again")
                        self._unload_llamacpp_models()
                        time.sleep(1)
                        continue
                    # len(running) == 0
                    out = subprocess.run(
                        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if out.returncode == 0:
                        free = int(out.stdout.strip().split("\n")[0].strip())
                        if free >= needed_mb:
                            self.logger.info(
                                f"VRAM check OK: {free}MB free, 0 LLM models loaded (needed={needed_mb}MB)"
                            )
                            return True
                        self.logger.info(f"VRAM: {free}MB free, need {needed_mb}MB — waiting for CUDA dealloc...")

                self.app.logger.info(f"VRAM: waiting... (needed={needed_mb}MB, timeout={timeout}s)")
            except Exception as e:
                self.app.logger.debug(f"VRAM poll error: {e}")
            time.sleep(1)

        self.logger.error(f"VRAM wait timeout ({timeout}s) — insufficient VRAM, returning False")
        return False

    # ── Task handlers extracted from _process_request ──

    def _process_image_edit_task(
        self,
        message_text: str,
        file_data: str,
        file_type: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
        task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle image editing request (image uploaded + edit comment)."""
        # Pre-operation monitoring and VRAM cleanup
        self._log_gpu_state_before_op("sd-edit", 8000)

        # Unload ALL GPU resources before multimodal model
        self._unload_llamacpp_models()
        self._unload_video_pipeline()

        self._publish_stream_event(task, "task_progress", {"stage": "preparing_gpu"})

        if task and self._is_task_cancelled(task["id"]):
            self._publish_stream_event(task, "stream_cancelled")
            return self._build_error_response(session_id, self.app.modules["base"]._("Task cancelled", lang=lang), 0, lang)

        # Wait for guaranteed free VRAM (multimodal model needs ~8GB with KV cache)
        if not self._wait_for_vram(self._get_vram_needed("multimodal")):
            error_msg = self.app.modules["base"]._("GPU memory unavailable. Try again in a moment.", lang=lang)
            return self._build_error_response(session_id, error_msg, 0, lang)

        mm_start = time.time()
        if task:
            self._publish_stream_event(task, "task_progress", {"stage": "analyzing_image"})
        edit_data, error = self.app.modules["multimodal"].generate_edit_params(message_text, file_data, lang=lang)
        mm_time = round(time.time() - mm_start, 1)
        if error:
            return self._build_error_response(session_id, error, mm_time, lang)

        if task and self._is_task_cancelled(task["id"]):
            self._publish_stream_event(task, "stream_cancelled")
            return self._build_error_response(session_id, self.app.modules["base"]._("Task cancelled", lang=lang), mm_time, lang)

        edit_start = time.time()
        if task:
            self._publish_stream_event(task, "task_progress", {"stage": "editing_image"})
        image_result = self.app.modules["image"].edit_image(
            edit_data,
            file_data,
            lang=lang,
            task_id=task.get("id") if task else None,
            user_id=user_id,
            session_id=session_id,
        )
        edit_time = round(time.time() - edit_start, 1)

        if not image_result["success"]:
            return self._build_error_response(
                session_id, image_result.get("error", self.app.modules["base"]._("Image editing failed", lang=lang)), mm_time + edit_time, lang
            )

        # Show resize notice if image was downscaled for editing
        resize_notice = None
        resize_notice_id = None
        if image_result.get("resized") and image_result.get("original_size") and image_result.get("new_size"):
            orig_w, orig_h = image_result["original_size"]
            new_w, new_h = image_result["new_size"]
            lang_for_msg = lang
            with force_locale(lang_for_msg):
                resize_text = (
                    self.app.modules["base"]
                    ._(
                        "Maximum resolution for editing is {max_w}×{max_h}. "
                        "The image has been resized from {orig_w}×{orig_h} to {new_w}×{new_h}.",
                        lang=lang_for_msg,
                    )
                    .format(max_w=1024, max_h=1024, orig_w=orig_w, orig_h=orig_h, new_w=new_w, new_h=new_h)
                )
            resize_notice_id = save_message(
                session_id, "assistant", resize_text, model_name="system", response_time="0"
            )
            resize_notice = resize_text

        template = self.app.modules["base"]._("Image edited from request: {query}", lang=lang)
        prefix = "🎨 " + template.replace("{query}", "")
        message_text_out = json.dumps({"prefix": prefix, "text": message_text}, ensure_ascii=False)
        file_path = None
        if image_result.get("image_data"):
            self.app.logger.info(f"Edit: saving image, data length={len(image_result['image_data'])}")
            file_path = save_uploaded_file(
                file_data=image_result["image_data"],
                filename=image_result["file_name"],
                session_id=session_id,
                upload_folder=self.app.config["UPLOAD_FOLDER"],
                user_id=user_id,
            )
            self.app.logger.info(f"Edit: saved to file_path={file_path}")
        else:
            self.app.logger.warning("Edit: no image_data in result")

        extra = {
            "file_path": file_path,
            "file_name": image_result["file_name"],
            "file_size": image_result["file_size"],
            "file_type": image_result["file_type"],
            "mm_time": mm_time,
            "gen_time": edit_time,
            "mm_model": image_result.get("mm_model"),
            "gen_model": "flux-2-klein-4b",
            "response_time": {
                "mm_time": mm_time,
                "gen_time": edit_time,
                "mm_model": image_result.get("mm_model"),
                "gen_model": "flux-2-klein-4b",
            },
            "resize_notice": resize_notice,
            "resize_notice_id": resize_notice_id,
        }
        return self._save_and_respond(
            session_id,
            message_text_out,
            "flux-2-klein-4b",
            {"mm_time": mm_time, "gen_time": edit_time},  # type: ignore[arg-type]
            file_data=None,
            file_type=image_result["file_type"],
            file_name=image_result["file_name"],
            file_path=file_path,
            extra={**extra, "model_type": "image_edit"},
            response_style=response_style,
        )

    def _process_image_gen_task(
        self,
        query: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
        task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle image generation from text (router action_type='image')."""
        if "image" not in self.app.modules:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Image generation module unavailable", lang=lang), 0, lang
            )
        self.app.modules["image"].check_availability()
        if not self.app.modules["image"].available:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Image generation module unavailable", lang=lang), 0, lang
            )

        # Pre-operation monitoring and VRAM cleanup
        self._log_gpu_state_before_op("sd-gen", 8000)

        # Unload ALL GPU resources before multimodal model
        self._unload_llamacpp_models()
        self._unload_video_pipeline()

        self._publish_stream_event(task, "task_progress", {"stage": "preparing_gpu"})

        if task and self._is_task_cancelled(task["id"]):
            self._publish_stream_event(task, "stream_cancelled")
            return self._build_error_response(session_id, self.app.modules["base"]._("Task cancelled", lang=lang), 0, lang)

        # Wait for guaranteed free VRAM (multimodal model needs ~8GB with KV cache)
        if not self._wait_for_vram(self._get_vram_needed("multimodal")):
            error_msg = self.app.modules["base"]._("GPU memory unavailable. Try again in a moment.", lang=lang)
            return self._build_error_response(session_id, error_msg, 0, lang)

        if not self._check_vram_ready(self._get_vram_needed("multimodal")):
            error_msg = self.app.modules["base"]._("GPU memory check failed. Please try again.", lang=lang)
            return self._build_error_response(session_id, error_msg, 0, lang)

        mm_start = time.time()
        if task:
            self._publish_stream_event(task, "task_progress", {"stage": "analyzing_prompt"})
        prompt_data, error = self.app.modules["multimodal"].generate_image_params(
            query, lang=lang, response_style=response_style
        )
        mm_time = round(time.time() - mm_start, 1)
        if error:
            return self._build_error_response(session_id, error, mm_time, lang)

        if task and self._is_task_cancelled(task["id"]):
            self._publish_stream_event(task, "stream_cancelled")
            return self._build_error_response(session_id, self.app.modules["base"]._("Task cancelled", lang=lang), mm_time, lang)

        gen_start = time.time()
        if task:
            self._publish_stream_event(task, "task_progress", {"stage": "generating_image"})
        image_result = self.app.modules["image"]._call_wrapper(
            prompt_data,
            lang=lang,
            task_id=task.get("id") if task else None,
            user_id=user_id,
            session_id=session_id,
        )
        gen_time = round(time.time() - gen_start, 1)

        if not image_result["success"]:
            return self._build_error_response(session_id, image_result["error"], mm_time + gen_time, lang)

        # Unload video pipeline after SD generation — frees VRAM for subsequent LLM
        self._unload_video_pipeline()

        sd_model = self.app.config.get("SD_MODEL_TYPE", "z_image_turbo")
        template = self.app.modules["base"]._("Image generated from request: {query}", lang=lang)
        prefix = "🎨 " + template.replace("{query}", "")
        message_text = json.dumps({"prefix": prefix, "text": query}, ensure_ascii=False)
        file_path = None
        if image_result.get("image_data"):
            file_path = save_uploaded_file(
                file_data=image_result["image_data"],
                filename=image_result["file_name"],
                session_id=session_id,
                upload_folder=self.app.config["UPLOAD_FOLDER"],
                user_id=user_id,
            )

        mm_model = self._get_model_name("multimodal") or "unknown"
        extra = {
            "file_path": file_path,
            "file_name": image_result["file_name"],
            "file_size": image_result["file_size"],
            "file_type": image_result["file_type"],
            "mm_time": mm_time,
            "gen_time": gen_time,
            "mm_model": mm_model,
            "gen_model": sd_model,
            "response_time": {"mm_time": mm_time, "gen_time": gen_time, "mm_model": mm_model, "gen_model": sd_model},
        }
        return self._save_and_respond(
            session_id,
            message_text,
            sd_model,
            {"mm_time": mm_time, "gen_time": gen_time},
            file_data=None,
            file_type=image_result["file_type"],
            file_name=image_result["file_name"],
            file_path=file_path,
            extra={**extra, "model_type": "image_gen"},
            response_style=response_style,
        )

    def _requeue_video_task(
        self,
        query: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
        file_data: str | None = None,
        file_type: str | None = None,
        file_name: str | None = None,
        user_class: int = 2,
    ) -> dict[str, Any]:
        """Re-queue a video generation task to the slow queue.
        Called when router detects [-VIDEO-] marker in text/image+text response.
        """
        request_data = {
            "type": "video",
            "text": query,
            "preview": (query[:50] + "...") if query else self.app.modules["base"]._("Video request", lang=lang),
            "response_style": response_style,
        }
        if file_data:
            request_data["file_data"] = file_data
            request_data["file_type"] = file_type
            request_data["file_name"] = file_name

        new_request_id, position_info = self.add_request(user_id, session_id, request_data, user_class, lang=lang)
        self.app.logger.info(
            f"Re-queued video task {new_request_id} for session {session_id} (position {position_info['position']})"
        )

        return {
            "status": "queued",
            "request_id": new_request_id,
            "position": position_info["position"],
            "estimated_wait": position_info["estimated_seconds"],
        }

    def _requeue_image_task(
        self,
        query: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
        user_class: int = 2,
    ) -> dict[str, Any]:
        """Re-queue an image generation task to the slow queue.
        Prevents concurrent sd-wrapper requests which cause timeouts.
        """
        request_data = {
            "type": "image_gen",
            "text": query,
            "preview": (query[:50] + "...") if query else self.app.modules["base"]._("Image request", lang=lang),
            "response_style": response_style,
        }
        new_request_id, position_info = self.add_request(user_id, session_id, request_data, user_class, lang=lang)
        self.app.logger.info(
            f"Re-queued image task {new_request_id} for session {session_id} (position {position_info['position']})"
        )
        return {
            "status": "queued",
            "request_id": new_request_id,
            "position": position_info["position"],
            "estimated_wait": position_info["estimated_seconds"],
        }

    def _requeue_reasoning_task(
        self,
        query: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
        user_class: int = 2,
        rag_context: str = "",
        rag_source: str = "",
        skip_rag: bool = False,
    ) -> dict[str, Any]:
        """Re-queue a reasoning task to the slow queue.
        Prevents GPU contention with SD/Video (all GPU ops are serialised
        through the slow worker).
        When skip_rag=True, the slow worker will NOT retry RAG search
        (used when RAG was already tried on the fast worker and found nothing).
        """
        request_data = {
            "type": "reasoning_task",
            "text": query,
            "preview": (query[:50] + "...") if query else self.app.modules["base"]._("Reasoning request", lang=lang),
            "response_style": response_style,
        }
        if rag_context:
            request_data["rag_context"] = rag_context
            request_data["rag_source"] = rag_source
        if skip_rag:
            request_data["skip_rag"] = True
        new_request_id, position_info = self.add_request(user_id, session_id, request_data, user_class, lang=lang)
        self.app.logger.info(
            f"Re-queued reasoning task {new_request_id} for session {session_id} (position {position_info['position']})"
        )
        return {
            "status": "queued",
            "request_id": new_request_id,
            "position": position_info["position"],
            "estimated_wait": position_info["estimated_seconds"],
        }

    def _process_image_gen_request(self, task: dict[str, Any]) -> dict[str, Any]:
        """Handle an image generation task from the slow queue."""
        request_data = task.get("data", {})
        query = request_data.get("text", "")
        session_id = task["session_id"]
        user_id = task["user_id"]
        lang = task.get("lang", "ru")
        response_style = request_data.get("response_style", "neutral")
        return self._process_image_gen_task(query, session_id, user_id, lang, response_style, task=task)

    def _process_reasoning_request(self, task: dict[str, Any]) -> dict[str, Any]:
        """Handle a reasoning task from the slow queue.

        Ensures VRAM is sufficient before loading the reasoning model
        (~10 GiB for gpt-oss-20b). Unloads chat and waits for SD/Video
        to free VRAM if needed. Passes RAG context to the reasoning model.
        """
        request_data = task.get("data", {})
        query = request_data.get("text", "")
        session_id = task["session_id"]
        user_id = task["user_id"]
        lang = task.get("lang", "ru")
        response_style = request_data.get("response_style", "neutral")

        # Pre-operation monitoring
        self._log_gpu_state_before_op("reasoning", 12000)

        self._publish_stream_event(task, "task_progress", {"stage": "loading_reasoning_model"})

        # Use pre-computed RAG context from fast worker, or search fresh
        rag_context = request_data.get("rag_context", "")
        rag_source = request_data.get("rag_source", "")
        skip_rag = request_data.get("skip_rag", False)
        if rag_context:
            self.app.logger.info(f"Using pre-computed RAG context: {len(rag_context)} chars from fast worker")
        elif skip_rag:
            self.app.logger.info("RAG already attempted on fast worker (no relevant docs) — skipping retry")
        else:
            # No pre-computed context — try RAG answer directly (covers non-requeue paths)
            rag_start = time.time()
            rag_answer, rag_model = self._try_rag_answer(
                query, session_id, user_id, lang, strict=True, response_style=response_style
            )
            rag_time = round(time.time() - rag_start, 1)
            if rag_answer is not None:
                if self._is_llm_error_string(rag_answer):
                    return self._build_error_response(session_id, rag_answer, rag_time, lang)
                model_name = rag_model if rag_model and rag_model.endswith(".gguf") else (rag_model + ".gguf" if rag_model else "")
                model_used = model_name + " (RAG)" if model_name else "unknown (RAG)"
                self.app.logger.info(f"RAG answered in reasoning request: {query[:50]}...")
                return self._save_and_respond(
                    session_id,
                    rag_answer,
                    model_used,
                    rag_time,
                    extra={"model_type": "reasoning"},
                    response_style=response_style,
                    user_id=user_id,
                )

            # No RAG answer — search raw chunks for reasoning model context
            rag = self.app.modules.get("rag")
            if rag and rag.available:
                try:
                    chunks, scores = rag.search(user_id, query, top_k=20)
                    if chunks:
                        from flask_babel import gettext as _

                        with force_locale(lang):
                            source_label = _("Source")
                        context_parts = []
                        for i, chunk in enumerate(chunks[:15]):  # top 15 chunks
                            filename = chunk.get("filename", "?")
                            text = chunk.get("text", str(chunk))
                            score = scores[i] if i < len(scores) else 0.0
                            context_parts.append(f"[{source_label}: {filename} (score: {score:.2f})]\n{text}")
                        rag_context = "\n\n".join(context_parts)
                        rag_source = "rag"
                        self.app.logger.info(
                            f"RAG raw context: {len(chunks)} chunks, {len(rag_context)} chars passed to reasoning model"
                        )
                except Exception as e:
                    self.app.logger.debug(f"RAG raw context collection failed: {e}")

        # Ensure VRAM before loading reasoning model (with improved checks)
        vram_ok = False
        try:
            from app.resource_manager import get_resource_manager

            rm = get_resource_manager()
            if rm:
                vram_ok = rm.ensure_vram_for_reasoning()
        except Exception as e:
            self.logger.error(f"VRAM check for reasoning failed: {e}")

        if not vram_ok:
            error_msg = self.app.modules["base"]._(
                "Reasoning model unavailable: GPU memory check failed. Try again.", lang=lang
            )
            return self._build_error_response(session_id, error_msg, 0, lang)

        current_time_str = get_current_time_in_timezone(self.app)
        stream_start = time.time()
        full_response = ""
        error_detected = False
        for token in self.app.modules["base"].generate_reasoning_response_stream(
            query,
            current_time_str,
            lang=lang,
            session_id=session_id,
            response_style=response_style,
            user_id=user_id,
            rag_context=rag_context,
            rag_source=rag_source,
        ):
            full_response += token
            if not error_detected:
                if self._is_llm_error_string(full_response):
                    error_detected = True
                else:
                    self._publish_stream_token(task, token)
            if self._is_task_cancelled(task["id"]):
                break
        reasoning_time = round(time.time() - stream_start, 1)
        full_response = self._strip_thinking_tags(full_response)
        if not full_response.strip():
            return self._build_error_response(
                session_id,
                self.app.modules["base"]._("No response from reasoning model", lang),
                reasoning_time,
                lang,
            )
        if self._is_llm_error_string(full_response):
            return self._build_error_response(session_id, full_response, reasoning_time, lang)

        result = self._save_and_respond(
            session_id,
            full_response,
            self._get_model_name("reasoning") or "reasoning",
            reasoning_time,
            extra={"model_type": "reasoning"},
            response_style=response_style,
            user_id=user_id,
        )

        # Extract facts in background thread (CPU-only, no GPU lock)
        if full_response.strip() and len(full_response) > 20:
            threading.Thread(
                target=_extract_facts_bg,
                args=(self.app, query, full_response[:3000], session_id, user_id, lang),
                daemon=True,
            ).start()

        return result

    def _process_video_request(self, task: dict[str, Any]) -> dict[str, Any]:
        """Handle a video task from the slow queue.
        Dispatches to text-to-video or image+text-to-video based on task data.
        """
        request_data = task.get("data", {})
        query = request_data.get("text", "")
        session_id = task["session_id"]
        user_id = task["user_id"]
        lang = task.get("lang", "ru")
        response_style = request_data.get("response_style", "neutral")

        self._publish_stream_event(task, "task_progress", {"stage": "preparing_gpu"})

        file_data = request_data.get("file_data")
        if file_data:
            return self._process_video_gen_task_from_image(
                query, file_data, session_id, user_id, lang, response_style, task=task
            )
        return self._process_video_gen_task(query, session_id, user_id, lang, response_style, task=task)

    def _process_video_gen_task(
        self,
        query: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
        task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle video generation from text (router action_type='video')."""
        if "video" not in self.app.modules:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Video generation module unavailable", lang=lang), 0, lang
            )

        # Pre-operation monitoring and cleanup
        self._log_gpu_state_before_op("video-gen", 10000)

        # Unload ALL GPU resources before loading multimodal
        self._unload_llamacpp_models()
        self._unload_video_pipeline()

        try:
            # Wait for guaranteed free VRAM with no LLM processes (multimodal model)
            if not self._wait_for_vram(self._get_vram_needed("multimodal")):
                error_msg = self.app.modules["base"]._("GPU memory unavailable. Try again in a moment.", lang=lang)
                return self._build_error_response(session_id, error_msg, 0, lang)

            # Final verification that VRAM is usable
            if not self._check_vram_ready(self._get_vram_needed("multimodal")):
                error_msg = self.app.modules["base"]._("GPU memory check failed. Please try again.", lang=lang)
                return self._build_error_response(session_id, error_msg, 0, lang)

            mm_start = time.time()
            if task:
                self._publish_stream_event(task, "task_progress", {"stage": "analyzing"})
            prompt_data, error = self.app.modules["multimodal"].generate_video_params(
                query, lang=lang, response_style=response_style
            )
            mm_time = round(time.time() - mm_start, 1)
            if error:
                return self._build_error_response(session_id, error, mm_time, lang)

            if task and self._is_task_cancelled(task["id"]):
                self._publish_stream_event(task, "stream_cancelled")
                return self._build_error_response(session_id, self.app.modules["base"]._("Task cancelled", lang=lang), mm_time, lang)

            # CRITICAL: Unload multimodal model AFTER params generated but BEFORE video.
            # generate_video_params() loaded Qwen3VL-8B (~5GB) — must free VRAM
            # before LTX-Video pipeline (~8GB) loads, or total > GPU capacity → OOM.
            self._unload_llamacpp_models()

            # Verify VRAM is truly free for video pipeline — must check BOTH:
            # (a) no LLM models loaded via llama-swap /running
            # (b) sufficient raw VRAM (estimated peak from files/measurements)
            if not self._wait_for_vram_full():
                error_msg = self.app.modules["base"]._(
                    "GPU memory unavailable after unloading LLM. Try again.", lang=lang
                )
                return self._build_error_response(session_id, error_msg, mm_time, lang)

            gen_start = time.time()
            if task:
                self._publish_stream_event(task, "task_progress", {"stage": "generating_video"})
            cancel_stop = self._start_cancel_checker(task["id"]) if task else None
            try:
                video_result = self.app.modules["video"].generate_video(
                    prompt_data, lang=lang, user_id=user_id, session_id=session_id, task_id=task.get("id") if task else None
                )
            finally:
                if cancel_stop:
                    cancel_stop.set()
            gen_time = round(time.time() - gen_start, 1)

            if task and self._is_task_cancelled(task["id"]):
                self._publish_stream_event(task, "stream_cancelled")
                return self._build_error_response(session_id, self.app.modules["base"]._("Task cancelled", lang=lang), mm_time + gen_time, lang)

            if not video_result["success"]:
                err_msg = video_result.get("error", "")
                if "CUDA out of memory" in str(err_msg):
                    err_msg = self.app.modules["base"]._(
                        "Video generation failed: GPU memory exhausted. Please simplify your request.", lang=lang
                    )
                    try:
                        from app.tasks.health_monitor import record_ltx_video_oom

                        record_ltx_video_oom()
                    except Exception:
                        pass
                return self._build_error_response(session_id, err_msg, mm_time + gen_time, lang)

            # Record peak VRAM for future estimates
            try:
                from app.resource_manager import get_resource_manager

                rm = get_resource_manager()
                video_model_name = self.app.config.get("LTX_VIDEO_MODEL", "ltxv-2b-0.9.8-distilled")
                rm.measure_video_vram_peak(video_model_name)
            except Exception as e:
                self.logger.debug(f"Video VRAM measurement failed: {e}")

            video_model = self.app.config.get("LTX_VIDEO_MODEL", "ltxv-2b-0.9.8-distilled")
            template = self.app.modules["base"]._("Video generated from request: {query}", lang=lang)
            prefix = "🎬 " + template.replace("{query}", "")
            message_text = json.dumps({"prefix": prefix, "text": query}, ensure_ascii=False)
            file_path = None
            if video_result.get("video_data"):
                file_path = save_uploaded_file(
                    file_data=video_result["video_data"],
                    filename=video_result["file_name"],
                    session_id=session_id,
                    upload_folder=self.app.config["UPLOAD_FOLDER"],
                    user_id=user_id,
                )

            mm_model = self._get_model_name("multimodal") or "unknown"
            extra = {
                "file_path": file_path,
                "file_name": video_result["file_name"],
                "file_size": video_result["file_size"],
                "file_type": video_result["file_type"],
                "mm_time": mm_time,
                "gen_time": gen_time,
                "mm_model": mm_model,
                "gen_model": video_model,
                "response_time": {
                    "mm_time": mm_time,
                    "gen_time": gen_time,
                    "mm_model": mm_model,
                    "gen_model": video_model,
                },
                "metadata": video_result.get("metadata", {}),
            }
            return self._save_and_respond(
                session_id,
                message_text,
                video_model,
                {"mm_time": mm_time, "gen_time": gen_time},
                file_data=None,
                file_type=video_result["file_type"],
                file_name=video_result["file_name"],
                file_path=file_path,
                extra={**extra, "model_type": "video"},
                response_style=response_style,
            )
        finally:
            # CRITICAL: Always unload video pipeline and LLM models, even on error.
            # Prevents VRAM leak when generation fails or returns early.
            self._unload_video_pipeline()
            self._unload_llamacpp_models()

    def _process_video_gen_task_from_image(
        self,
        query: str,
        image_data: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
        task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle video generation from image + text ([-VIDEO-] marker route)."""
        if "video" not in self.app.modules:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Video generation module unavailable", lang=lang), 0, lang
            )

        # Pre-operation monitoring and VRAM cleanup
        self._log_gpu_state_before_op("video-gen-from-img", 10000)

        # Unload ALL GPU resources before loading multimodal
        self._unload_llamacpp_models()
        self._unload_video_pipeline()

        try:
            # Wait for guaranteed free VRAM (multimodal model needs ~8GB with KV cache)
            if not self._wait_for_vram(self._get_vram_needed("multimodal")):
                error_msg = self.app.modules["base"]._("GPU memory unavailable. Try again in a moment.", lang=lang)
                return self._build_error_response(session_id, error_msg, 0, lang)

            if not self._check_vram_ready(self._get_vram_needed("multimodal")):
                error_msg = self.app.modules["base"]._("GPU memory check failed. Please try again.", lang=lang)
                return self._build_error_response(session_id, error_msg, 0, lang)

            mm_start = time.time()
            if task:
                self._publish_stream_event(task, "task_progress", {"stage": "analyzing_image"})
            mm_model = self.app.modules.get("multimodal")
            prompt_data, error = (
                mm_model.generate_video_params_from_image(query, image_data, lang=lang, response_style=response_style)
                if mm_model
                else (None, self.app.modules["base"]._("Multimodal model unavailable", lang=lang))
            )
            mm_time = round(time.time() - mm_start, 1)
            if error:
                return self._build_error_response(session_id, error, mm_time, lang)

            if task and self._is_task_cancelled(task["id"]):
                self._publish_stream_event(task, "stream_cancelled")
                return self._build_error_response(session_id, self.app.modules["base"]._("Task cancelled", lang=lang), mm_time, lang)

            # CRITICAL: Unload multimodal model AFTER params generated but BEFORE video.
            # generate_video_params_from_image() loaded Qwen3VL-8B (~5GB) — must free VRAM
            # before LTX-Video pipeline (~8GB) loads, or total > GPU capacity → OOM.
            self._unload_llamacpp_models()

            if not self._wait_for_vram_full():
                error_msg = self.app.modules["base"]._(
                    "GPU memory unavailable after unloading LLM. Try again.", lang=lang
                )
                return self._build_error_response(session_id, error_msg, mm_time, lang)

            gen_start = time.time()
            if task:
                self._publish_stream_event(task, "task_progress", {"stage": "generating_video"})
            cancel_stop = self._start_cancel_checker(task["id"]) if task else None
            try:
                video_result = self.app.modules["video"].generate_video(
                    prompt_data,
                    image_data=image_data,
                    lang=lang,
                    user_id=user_id,
                    session_id=session_id,
                    task_id=task.get("id") if task else None,
                )
            finally:
                if cancel_stop:
                    cancel_stop.set()
            gen_time = round(time.time() - gen_start, 1)

            if task and self._is_task_cancelled(task["id"]):
                self._publish_stream_event(task, "stream_cancelled")
                return self._build_error_response(session_id, self.app.modules["base"]._("Task cancelled", lang=lang), mm_time + gen_time, lang)

            if not video_result["success"]:
                err_msg = video_result.get("error", "")
                if "CUDA out of memory" in str(err_msg):
                    err_msg = self.app.modules["base"]._(
                        "Video generation failed: GPU memory exhausted. Please simplify your request.", lang=lang
                    )
                    try:
                        from app.tasks.health_monitor import record_ltx_video_oom

                        record_ltx_video_oom()
                    except Exception:
                        pass
                return self._build_error_response(session_id, err_msg, mm_time + gen_time, lang)

            # Record peak VRAM for future estimates
            try:
                from app.resource_manager import get_resource_manager

                rm = get_resource_manager()
                video_model_name = self.app.config.get("LTX_VIDEO_MODEL", "ltxv-2b-0.9.8-distilled")
                rm.measure_video_vram_peak(video_model_name)
            except Exception as e:
                self.logger.debug(f"Video VRAM measurement failed: {e}")

            # Show resize notice if source image was downscaled for video
            resize_notice = None
            resize_notice_id = None
            if video_result.get("resized") and video_result.get("original_size") and video_result.get("new_size"):
                orig_w, orig_h = video_result["original_size"]
                new_w, new_h = video_result["new_size"]
                lang_for_msg = lang
                with force_locale(lang_for_msg):
                    resize_text = (
                        self.app.modules["base"]
                        ._(
                            "Maximum resolution for video is {max_w}×{max_h}. "
                            "The image has been resized from {orig_w}×{orig_h} to {new_w}×{new_h}.",
                            lang=lang_for_msg,
                        )
                        .format(max_w=768, max_h=768, orig_w=orig_w, orig_h=orig_h, new_w=new_w, new_h=new_h)
                    )
                resize_notice_id = save_message(
                    session_id, "assistant", resize_text, model_name="system", response_time="0"
                )
                resize_notice = resize_text

            video_model = self.app.config.get("LTX_VIDEO_MODEL", "ltxv-2b-0.9.8-distilled")
            template = self.app.modules["base"]._("Video generated from request: {query}", lang=lang)
            prefix = "🎬 " + template.replace("{query}", "")
            message_text = json.dumps({"prefix": prefix, "text": query}, ensure_ascii=False)
            file_path = None
            if video_result.get("video_data"):
                file_path = save_uploaded_file(
                    file_data=video_result["video_data"],
                    filename=video_result["file_name"],
                    session_id=session_id,
                    upload_folder=self.app.config["UPLOAD_FOLDER"],
                    user_id=user_id,
                )

            mm_model_name = self._get_model_name("multimodal") or "unknown"
            extra = {
                "file_path": file_path,
                "file_name": video_result["file_name"],
                "file_size": video_result["file_size"],
                "file_type": video_result["file_type"],
                "mm_time": mm_time,
                "gen_time": gen_time,
                "mm_model": mm_model_name,
                "gen_model": video_model,
                "response_time": {
                    "mm_time": mm_time,
                    "gen_time": gen_time,
                    "mm_model": mm_model_name,
                    "gen_model": video_model,
                },
                "metadata": video_result.get("metadata", {}),
                "resize_notice": resize_notice,
                "resize_notice_id": resize_notice_id,
            }
            return self._save_and_respond(
                session_id,
                message_text,
                video_model,
                {"mm_time": mm_time, "gen_time": gen_time},
                file_data=None,
                file_type=video_result["file_type"],
                file_name=video_result["file_name"],
                file_path=file_path,
                extra={**extra, "model_type": "video"},
                response_style=response_style,
            )
        finally:
            # CRITICAL: Always unload video pipeline and LLM models, even on error.
            # Prevents VRAM leak when generation fails or returns early.
            self._unload_video_pipeline()
            self._unload_llamacpp_models()

    def _process_camera_task(
        self,
        query: str,
        session_id: str,
        user_id: str,
        message_text: str,
        current_time_str: str,
        lang: str,
        response_style: str = "neutral",
    ) -> dict[str, Any]:
        """Handle camera snapshot request (router action_type='camera')."""
        if "cam" not in self.app.modules or not self.app.modules["cam"].available:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Camera module unavailable", lang=lang), 0, lang
            )

        camera_start = time.time()
        camera_result = self.app.modules["cam"].get_snapshot(user_id, query, lang=lang)
        camera_time = round(time.time() - camera_start, 1)

        if not camera_result["success"]:
            return self._build_error_response(session_id, camera_result["error"], camera_time, lang)

        template = self.app.modules["base"]._("Camera snapshot: {room_name}", lang=lang)
        prefix = template.replace("{room_name}", "")
        translated_text = json.dumps({"prefix": prefix, "text": camera_result["room_name"]}, ensure_ascii=False)
        file_path = None
        if camera_result.get("image_data"):
            file_path = save_uploaded_file(
                file_data=camera_result["image_data"],
                filename=camera_result["file_name"],
                session_id=session_id,
                upload_folder=self.app.config["UPLOAD_FOLDER"],
                user_id=user_id,
            )

        first_message = self._save_and_respond(
            session_id,
            translated_text,
            "camera",
            camera_time,
            file_data=None,
            file_type=camera_result["image_type"],
            file_name=camera_result["file_name"],
            file_path=file_path,
            extra={
                "file_path": file_path,
                "file_name": camera_result["file_name"],
                "file_size": camera_result["file_size"],
                "file_type": camera_result["image_type"],
                "response_time": camera_time,
                "model_type": "camera",
            },
            response_style=response_style,
        )

        messages = [first_message]
        if message_text and "multimodal" in self.app.modules and self.app.modules["multimodal"].available:
            self._unload_llamacpp_models()
            self._unload_video_pipeline()
            if not self._wait_for_vram(self._get_vram_needed("multimodal")):
                error_msg = self.app.modules["base"]._("GPU memory unavailable. Try again in a moment.", lang=lang)
                return {
                    "messages": [self._build_error_response(session_id, error_msg, 0, lang)],
                    "session_id": session_id,
                }
            mm_start = time.time()
            bot_reply, error = self.app.modules["multimodal"].process_image_with_text(
                camera_result["image_data"],
                message_text,
                current_time_str,
                lang=lang,
                session_id=None,
                response_style=response_style,
            )
            mm_time = round(time.time() - mm_start, 1)
            if error:
                bot_reply = f"⚠️ {error}"
            mm_model = self._get_model_name("multimodal") or "unknown"
            second = self._save_and_respond(
                session_id,
                bot_reply,
                mm_model,
                mm_time,
                is_error=bool(error),
                extra={"model_type": "multimodal"},
                response_style=response_style,
            )
            second["response_time"] = mm_time
            messages.append(second)

        return {"messages": messages, "session_id": session_id}

    def _process_camera_task_stream(
        self,
        task: dict[str, Any],
        query: str,
        session_id: str,
        user_id: str,
        message_text: str,
        current_time_str: str,
        lang: str,
        response_style: str = "neutral",
    ) -> dict[str, Any]:
        """Handle camera: show image immediately, then stream description."""
        if "cam" not in self.app.modules or not self.app.modules["cam"].available:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Camera module unavailable", lang=lang), 0, lang
            )

        self._publish_stream_event(task, "task_progress", {"stage": "capturing_snapshot"})

        camera_start = time.time()
        camera_result = self.app.modules["cam"].get_snapshot(user_id, query, lang=lang)
        camera_time = round(time.time() - camera_start, 1)

        if not camera_result["success"]:
            return self._build_error_response(session_id, camera_result["error"], camera_time, lang)

        template = self.app.modules["base"]._("Camera snapshot: {room_name}", lang=lang)
        prefix = template.replace("{room_name}", "")
        translated_text = json.dumps({"prefix": prefix, "text": camera_result["room_name"]}, ensure_ascii=False)
        file_path = None
        if camera_result.get("image_data"):
            file_path = save_uploaded_file(
                file_data=camera_result["image_data"],
                filename=camera_result["file_name"],
                session_id=session_id,
                upload_folder=self.app.config["UPLOAD_FOLDER"],
                user_id=user_id,
            )

        image_result = self._save_and_respond(
            session_id,
            translated_text,
            "camera",
            camera_time,
            file_data=None,
            file_type=camera_result["image_type"],
            file_name=camera_result["file_name"],
            file_path=file_path,
            extra={
                "file_path": file_path,
                "file_name": camera_result["file_name"],
                "file_size": camera_result["file_size"],
                "file_type": camera_result["image_type"],
                "response_time": camera_time,
                "model_type": "camera",
            },
            response_style=response_style,
        )

        # No text or no multimodal → return both together (original behavior)
        if not message_text or "multimodal" not in self.app.modules or not self.app.modules["multimodal"].available:
            return {"messages": [image_result], "session_id": session_id}

        # Publish image immediately via custom SSE event
        self._publish_stream_event(
            task,
            "camera_image",
            {
                "response": translated_text,
                "session_id": session_id,
                "model_used": "camera",
                "model_type": "camera",
                "response_time": camera_time,
                "message_id": image_result.get("message_id"),
                "assistant_timestamp": image_result.get("assistant_timestamp"),
                "file_path": file_path,
                "file_name": camera_result["file_name"],
                "file_type": camera_result["image_type"],
                "response_style": response_style,
            },
        )

        # Stream description from multimodal model
        self._unload_llamacpp_models()
        self._unload_video_pipeline()
        if not self._wait_for_vram(self._get_vram_needed("multimodal")):
            error_msg = self.app.modules["base"]._("GPU memory unavailable. Try again in a moment.", lang=lang)
            return self._build_error_response(session_id, error_msg, 0, lang)
        stream_start = time.time()
        full_response = ""
        error_detected = False
        cancelled = False
        for token in self.app.modules["multimodal"].process_image_with_text_stream(
            camera_result["image_data"],
            message_text,
            current_time_str,
            lang=lang,
            session_id=None,
            response_style=response_style,
        ):
            full_response += token
            if not error_detected:
                if self._is_llm_error_string(full_response):
                    error_detected = True
                else:
                    self._publish_stream_token(task, token)
            if self._is_task_cancelled(task["id"]):
                cancelled = True
                break
        mm_time = round(time.time() - stream_start, 1)
        model_used = self._get_model_name("multimodal") or "unknown"
        if cancelled:
            self.app.logger.info(f"Task {task['id']} cancelled during camera stream")
            self._publish_stream_event(task, "stream_cancelled")
        if self._is_llm_error_string(full_response):
            return self._build_error_response(session_id, full_response, mm_time, lang)
        return self._save_and_respond(
            session_id,
            full_response,
            model_used,
            mm_time,
            extra={"model_type": "camera"},
            response_style=response_style,
        )

    def _process_rag_task(
        self, query: str, session_id: str, user_id: str, lang: str, response_style: str = "neutral"
    ) -> dict[str, Any]:
        """Handle explicit RAG request (router action_type='rag').

        Performs RAG search (embedding + Qdrant) on the fast worker,
        then re-queues to the slow worker for reasoning model generation.
        This avoids GPU contention: the reasoning model (~13 GiB) is loaded
        only in the slow worker where VRAM is properly managed.
        When no relevant documents found (below threshold), falls back to reasoning model.
        """
        rag = self.app.modules.get("rag")
        if not rag or not rag.available:
            self.app.logger.warning("RAG not available — falling back to reasoning")
            return self._requeue_reasoning_task(
                query, session_id, user_id, lang, response_style, skip_rag=True,
            )

        # Step 1: RAG search only (embedding ~500MB — safe on fast worker)
        rag_context = ""
        rag_threshold = self.app.config.get("RAG_RELEVANCE_THRESHOLD_DEFAULT", 0.3)
        try:
            chunks, scores = rag.search(user_id, query, top_k=20)
            filtered = [(c, s) for c, s in zip(chunks, scores, strict=False) if s >= rag_threshold]
            if filtered:
                from flask_babel import gettext as _

                with force_locale(lang):
                    source_label = _("Source")
                context_parts = []
                for _i, (chunk, score) in enumerate(filtered[:15]):
                    filename = chunk.get("filename", "?")
                    text = chunk.get("text", str(chunk))
                    context_parts.append(f"[{source_label}: {filename} (score: {score:.2f})]\n{text}")
                rag_context = "\n\n".join(context_parts)
                self.app.logger.info(
                    f"RAG _process_rag_task: {len(chunks)} raw, {len(filtered)} filtered (>={rag_threshold}), "
                    f"{len(rag_context)} chars — requeueing to slow worker"
                )
            else:
                self.app.logger.warning(
                    f"RAG _process_rag_task: {len(chunks)} chunks but none above threshold {rag_threshold} "
                    f"for query: {query[:100]}... — falling back to reasoning"
                )
                return self._requeue_reasoning_task(
                    query, session_id, user_id, lang, response_style, skip_rag=True,
                )
        except Exception as e:
            self.logger.error(f"RAG search failed: {e}")
            self.app.logger.warning(
                f"RAG search failed, falling back to reasoning: {e}"
            )
            return self._requeue_reasoning_task(
                query, session_id, user_id, lang, response_style, skip_rag=True,
            )

        # Step 2: Re-queue to slow worker for reasoning model generation
        return self._requeue_reasoning_task(
            query,
            session_id,
            user_id,
            lang,
            response_style,
            rag_context=rag_context,
            rag_source="rag",
        )

    def _process_search_task(
        self, query: str, session_id: str, user_id: str, lang: str, response_style: str = "neutral"
    ) -> dict[str, Any]:
        """Handle web search request (router action_type='search').

        Searches SearXNG on the fast worker (CPU-only HTTP call),
        then re-queues to the slow worker for reasoning model synthesis.
        """
        search = self.app.modules.get("search")
        if not search or not search.available:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Web search is not available", lang), 0, lang
            )

        search_start = time.time()
        try:
            results = search.search(query, lang=lang)
            search_time = round(time.time() - search_start, 1)
            if not results:
                self.app.logger.warning(f"SearXNG returned 0 results for: {query[:100]}...")
                return self._build_error_response(
                    session_id,
                    self.app.modules["base"]._("No web search results found", lang),
                    search_time,
                    lang,
                )
            search_context = search.format_results_context(results, lang=lang)
            self.app.logger.info(
                f"Web search: '{query[:60]}...' → {len(results)} results, "
                f"{len(search_context)} chars — requeueing to slow worker ({search_time}s)"
            )
        except Exception as e:
            search_time = round(time.time() - search_start, 1)
            self.logger.error(f"Web search failed: {e}")
            return self._build_error_response(
                session_id,
                self.app.modules["base"]._("Web search failed", lang),
                search_time,
                lang,
            )

        return self._requeue_reasoning_task(
            query,
            session_id,
            user_id,
            lang,
            response_style,
            rag_context=search_context,
            rag_source="web_search",
        )

    def _process_rag_task_stream(
        self,
        task: dict[str, Any],
        query: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
    ) -> dict[str, Any]:
        """Handle explicit RAG request — search on fast worker, generation on slow worker.

        Performs RAG search (embedding + Qdrant) on the fast worker,
        then re-queues to the slow worker for reasoning model generation.
        When no relevant documents found (below threshold), falls back to reasoning model.
        """
        rag = self.app.modules.get("rag")
        if not rag or not rag.available:
            self.app.logger.warning("RAG not available — falling back to reasoning")
            return self._requeue_reasoning_task(
                query, session_id, user_id, lang, response_style, skip_rag=True,
            )

        # Step 1: RAG search only (embedding ~500MB — safe on fast worker)
        rag_context = ""
        rag_threshold = self.app.config.get("RAG_RELEVANCE_THRESHOLD_DEFAULT", 0.3)
        try:
            chunks, scores = rag.search(user_id, query, top_k=20)
            filtered = [(c, s) for c, s in zip(chunks, scores, strict=False) if s >= rag_threshold]
            if filtered:
                from flask_babel import gettext as _

                with force_locale(lang):
                    source_label = _("Source")
                context_parts = []
                for _i, (chunk, score) in enumerate(filtered[:15]):
                    filename = chunk.get("filename", "?")
                    text = chunk.get("text", str(chunk))
                    context_parts.append(f"[{source_label}: {filename} (score: {score:.2f})]\n{text}")
                rag_context = "\n\n".join(context_parts)
                self.app.logger.info(
                    f"RAG _process_rag_task_stream: {len(chunks)} raw, {len(filtered)} filtered (>={rag_threshold}), "
                    f"{len(rag_context)} chars — requeueing to slow worker"
                )
            else:
                self.app.logger.warning(
                    f"RAG _process_rag_task_stream: {len(chunks)} chunks but none above threshold {rag_threshold} "
                    f"for query: {query[:100]}... — falling back to reasoning"
                )
                return self._requeue_reasoning_task(
                    query, session_id, user_id, lang, response_style, skip_rag=True,
                )
        except Exception as e:
            self.logger.error(f"RAG search failed: {e}")
            self.app.logger.warning(
                f"RAG search failed, falling back to reasoning: {e}"
            )
            return self._requeue_reasoning_task(
                query, session_id, user_id, lang, response_style, skip_rag=True,
            )

        # Step 2: Re-queue to slow worker for reasoning model generation
        return self._requeue_reasoning_task(
            query,
            session_id,
            user_id,
            lang,
            response_style,
            rag_context=rag_context,
            rag_source="rag",
        )

    def _process_text_task(
        self,
        message_text: str,
        session_id: str,
        user_id: str,
        current_time_str: str,
        lang: str,
        response_style: str = "neutral",
        user_class: int = 2,
    ) -> dict[str, Any]:
        """Handle text request — routes through base module router."""
        router_start = time.time()
        router_result = self.app.modules["base"].process_message(
            message_text,
            current_time_str,
            lang=lang,
            session_id=session_id,
            response_style=response_style,
            user_id=user_id,
        )
        router_time = round(time.time() - router_start, 1)

        if "error" in router_result:
            return self._build_error_response(session_id, router_result["error"], router_time, lang)

        action_type = router_result["action"]
        query = router_result["query"]

        if action_type == "reasoning":
            return self._requeue_reasoning_task(query, session_id, user_id, lang, response_style, user_class=user_class, skip_rag=True)
        elif action_type == "image":
            return self._requeue_image_task(query, session_id, user_id, lang, response_style, user_class=user_class)
        elif action_type == "video":
            return self._requeue_video_task(query, session_id, user_id, lang, response_style, user_class=user_class)
        elif action_type == "camera":
            return self._process_camera_task(
                query, session_id, user_id, message_text, current_time_str, lang, response_style
            )
        elif action_type == "rag":
            return self._process_rag_task(query, session_id, user_id, lang, response_style)
        elif action_type == "search":
            return self._process_search_task(query, session_id, user_id, lang, response_style)
        else:
            # Simple query: router classified but did not generate text.
            # Call chat model WITHOUT tools — simple queries don't need them
            # and the extra ~1000 tokens of tool definitions confuse small models.
            return self._process_chat_with_tools(
                task={"id": uuid.uuid4().hex, "user_id": user_id, "session_id": session_id},
                query=query,
                current_time_str=current_time_str,
                session_id=session_id,
                user_id=user_id,
                lang=lang,
                response_style=response_style,
                stream=False,
                include_tools=False,
                expose_tools=True,
            )

    def _process_text_task_stream(
        self,
        task: dict[str, Any],
        message_text: str,
        session_id: str,
        user_id: str,
        current_time_str: str,
        lang: str,
        response_style: str = "neutral",
    ) -> dict[str, Any]:
        """Handle text request with streaming for the final LLM response."""
        router_start = time.time()
        router_result = self.app.modules["base"].process_message(
            message_text,
            current_time_str,
            lang=lang,
            session_id=session_id,
            response_style=response_style,
            user_id=user_id,
        )
        router_time = round(time.time() - router_start, 1)

        if "error" in router_result:
            return self._build_error_response(session_id, router_result["error"], router_time, lang)

        action_type = router_result["action"]
        query = router_result["query"]

        # Reasoning — re-queue to slow worker (reasoning model, ~10 GiB VRAM).
        if action_type == "reasoning":
            return self._requeue_reasoning_task(
                query,
                session_id,
                user_id,
                lang,
                response_style,
                user_class=task.get("user_class", 2),
                skip_rag=True,
            )

        # Simple query: router classified but did not generate text.
        # Use chat model with tool calling support.
        if action_type == "none":
            return self._process_chat_with_tools(
                task,
                query,
                current_time_str,
                session_id,
                user_id,
                lang,
                response_style,
                expose_tools=True,
            )

        # Explicit remember request — process through LLM and save to SLM
        if action_type == "remember":
            return self._process_remember_task(
                task,
                query,
                current_time_str,
                session_id,
                user_id,
                lang,
                response_style,
            )

        # Stream-aware actions — dispatch directly, no second router call
        if action_type == "rag":
            return self._process_rag_task_stream(task, query, session_id, user_id, lang, response_style)

        if action_type == "search":
            return self._process_search_task(query, session_id, user_id, lang, response_style)

        if action_type == "image":
            return self._requeue_image_task(
                query, session_id, user_id, lang, response_style, user_class=task.get("user_class", 2)
            )

        if action_type == "video":
            return self._requeue_video_task(
                query, session_id, user_id, lang, response_style, user_class=task.get("user_class", 2)
            )

        if action_type == "camera":
            return self._process_camera_task_stream(
                task, query, session_id, user_id, message_text, current_time_str, lang, response_style
            )

        # Unknown action — safe fallback that still avoids a second router call
        return self._save_and_respond(
            session_id,
            query,
            self._get_model_name("chat") or "unknown",
            router_time,
            extra={"model_type": "chat"},
            response_style=response_style,
            user_id=user_id,
        )

    def _process_chat_with_tools(
        self,
        task: dict[str, Any],
        query: str,
        current_time_str: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
        stream: bool = True,
        include_tools: bool = True,
        expose_tools: bool | None = None,
    ) -> dict[str, Any]:
        """Chat model with tool calling loop.

        Calls the chat model with tools. If the model returns tool_calls,
        executes them and feeds results back. Repeats until the model
        returns a final content response (max MAX_TOOL_ITERATIONS rounds).

        ``include_tools`` controls whether tool/time_calc instructions are
        included in the system prompt.
        ``expose_tools`` controls whether tool definitions are passed to the
        model.  If None, defaults to ``include_tools``.
        """
        from modules.base import STYLE_INSTRUCTIONS

        base = self.app.modules["base"]
        llamacpp = base.llamacpp

        # Build system + user messages for tool calling
        response_language = "Russian" if lang == "ru" else "English"
        context_str = base._get_context_for_model(  # noqa: SLF001
            session_id,
            "chat",
            query,
            lang,
            user_id=user_id,
            skip_slm=False,
        )
        style_instruction = STYLE_INSTRUCTIONS.get(lang, STYLE_INSTRUCTIONS["ru"]).get(
            response_style, STYLE_INSTRUCTIONS[lang]["neutral"]
        )

        if lang == "ru":
            task_instruction = (
                "Задача: Ответь на запрос пользователя."
                if not include_tools else
                "Задача: Ответь на запрос пользователя. Используй инструменты когда это необходимо — не придумывай ответ, лучше вызови инструмент."
            )
            system_parts = [
                f"# ИНСТРУКЦИЯ\n"
                f"Язык ответа: {response_language}.\n"
                f"Стиль ответа: {style_instruction}\n"
                f"Формат ответа: Пиши ТОЛЬКО готовый ответ. Не пиши рассуждений, анализа, шагов мышления, планов. Не объясняй, как ты пришёл к ответу. НЕ начинай ответ со слов «Пользователь спросил/спрашивает/просит», «Мне нужно ответить», «Анализ:», «Формулировка:», «Проверка:», «Коррекция:», «Финальный ответ:» и т.д. Сразу переходи к ответу по существу.\n"
                f"{task_instruction}",
            ]
            if include_tools:
                system_parts.append(
                    "\n\n# ПРАВИЛА ИСПОЛЬЗОВАНИЯ ИНСТРУМЕНТОВ\n"
                    "1. После получения результата инструмента — используй его напрямую в ответе.\n"
                    "2. НЕ ПЕРЕСЧЫТЫВАЙ результат инструмента самостоятельно.\n"
                    "3. НЕ ПРИДУМЫВАЙ ответ вместо использования результата инструмента.\n"
                    "4. Если инструмент вернул число — ответь этим числом.\n\n"
                    "# ВЫБОР ОПЕРАЦИИ time_calc\n"
                    "ВНИМАТЕЛЬНО выбирай операцию по запросу:\n"
                    "- 'до понедельника/вторника/.../ближайшей пятницы' → days_until_weekday\n"
                    "- 'до 30 июня/до конкретной даты' → days_until_date\n"
                    "- 'до конца года/месяца/лета/зимы' → days_until_end_of\n"
                    "- 'назад закончилась весна/лето' → days_since_end_of\n"
                    "- 'какой день недели' → day_of_week\n"
                    "- 'между 11 и 15 июня' → days_between\n"
                    "- 'через 5 дней какая дата' → add_days\n"
                    "- 'какое сегодня число' → format_date"
                )
            system_parts.append(
                f"\n\n# РОЛЬ\n"
                f"Ты персональный ассистент на основе искусственного интеллекта 'Полностью Локальный ИИ (ПЛИИ)'.\n\n"
                f"# НАВЫКИ\n"
                f"{_load_skills_section(lang)}\n\n"
                f"Текущее время (сейчас): {current_time_str}.\n\n"
                f"# ИСТОРИЯ ДИАЛОГА\n"
                f"{context_str}"
            )
            system_content = "".join(system_parts)
        else:
            task_instruction = (
                "Task: Answer the user's request."
                if not include_tools else
                "Task: Answer the user's request. Use tools when necessary — do not make up answers, call a tool instead."
            )
            system_parts = [
                f"# INSTRUCTION\n"
                f"Response language: {response_language}.\n"
                f"Response style: {style_instruction}\n"
                f"Response format: Write ONLY the final answer. Do NOT write reasoning, analysis, thinking steps, or plans. Do NOT explain how you arrived at the answer. Do NOT start with 'The user asked/asks/says...', 'I need to answer...', 'Analyze:', 'Formulate:', 'Check:', 'Self-Correction:', 'Final Answer:' etc. Go straight to the answer.\n"
                f"{task_instruction}",
            ]
            if include_tools:
                system_parts.append(
                    "\n\n# TOOL USAGE RULES\n"
                    "1. After receiving a tool result — use it directly in your answer.\n"
                    "2. Do NOT recalculate the tool result yourself.\n"
                    "3. Do NOT make up an answer instead of using the tool result.\n"
                    "4. If a tool returns a number — answer with that number.\n\n"
                    "# TIME_CALC OPERATION SELECTION\n"
                    "Choose the operation carefully based on the query:\n"
                    "- 'until Monday/Tuesday/.../next Friday' → days_until_weekday\n"
                    "- 'until June 30/until specific date' → days_until_date\n"
                    "- 'until end of year/month/summer/winter' → days_until_end_of\n"
                    "- 'days since spring/summer ended' → days_since_end_of\n"
                    "- 'what day of week is it' → day_of_week\n"
                    "- 'between June 11 and 15' → days_between\n"
                    "- 'what date in 5 days' → add_days\n"
                    "- 'what is today date' → format_date"
                )
            system_parts.append(
                f"\n\n# ROLE\n"
                f"You are a personal assistant based on artificial intelligence 'Fully Local AI (FLAI)'.\n\n"
                f"# SKILLS\n"
                f"{_load_skills_section(lang)}\n\n"
                f"Current time (now): {current_time_str}.\n\n"
                f"# CONVERSATION HISTORY\n"
                f"{context_str}"
            )
            system_content = "".join(system_parts)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": query},
        ]
        tools = get_tool_definitions(lang) if (expose_tools if expose_tools is not None else include_tools) else None

        stream_start = time.time()
        full_response = ""
        last_tool_result = ""
        streamed = False

        for iteration in range(MAX_TOOL_ITERATIONS):
            if self._is_task_cancelled(task["id"]):
                break

            # Call chat model with tools (non-streaming for tool detection)
            response = llamacpp.chat(messages, model_type="chat", lang=lang, tools=tools)

            # If response is an error string
            if isinstance(response, str):
                if self._is_llm_error_string(response):
                    return self._build_error_response(
                        session_id,
                        response,
                        round(time.time() - stream_start, 1),
                        lang,
                    )

                # Detect raw JSON tool call in text (small models sometimes output tool calls as text)
                parsed_tool_call = self._try_parse_text_tool_call(response)
                if parsed_tool_call:
                    tool_calls = [parsed_tool_call]
                    messages.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": tool_calls,
                        }
                    )
                    for tc in tool_calls:
                        tc_id = tc.get("id", "")
                        func = tc.get("function", {})
                        tool_name = func.get("name", "")
                        try:
                            arguments = json.loads(func.get("arguments", "{}"))
                        except json.JSONDecodeError:
                            arguments = {}
                        self._publish_stream_event(
                            task,
                            "tool_call",
                            {
                                "tool_name": tool_name,
                                "arguments": arguments,
                            },
                        )
                        self.logger.info(f"Tool call (text-parsed): {tool_name}({arguments})")
                        tool_context = {"app": self.app, "user_id": user_id, "lang": lang}
                        tool_result = execute_tool(tool_name, arguments, tool_context)
                        last_tool_result = tool_result
                        self.logger.info(f"Tool result: {tool_result[:200]}")
                        self._publish_stream_event(
                            task,
                            "tool_result",
                            {
                                "tool_name": tool_name,
                                "result_preview": tool_result[:200] + "..." if len(tool_result) > 200 else tool_result,
                            },
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": tool_result,
                            }
                        )
                    continue

                full_response = response
                # If strip_generic_reasoning returned empty (model produced only plan/analysis),
                # fall back to empty and let the empty-response handler at the end deal with it.
                if not full_response.strip():
                    self.logger.warning("Chat model returned empty response after reasoning strip")
                # Check cancel after blocking chat() call — flag may have been set during generation
                if self._is_task_cancelled(task["id"]):
                    self._publish_stream_event(task, "stream_cancelled")
                    break
                # Stream the final response to client
                for i, char in enumerate(full_response):
                    if i % 20 == 0 and self._is_task_cancelled(task["id"]):
                        self._publish_stream_event(task, "stream_cancelled")
                        break
                    self._publish_stream_token(task, char)
                    if i % 20 == 19:
                        time.sleep(0.01)
                streamed = True
                break

            # If response is a dict with tool_calls
            if isinstance(response, dict) and response.get("tool_calls"):
                tool_calls = response["tool_calls"]
                content = response.get("content", "")

                # Add assistant message with tool_calls to history
                messages.append(
                    {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    }
                )

                # Execute each tool
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    try:
                        arguments = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        arguments = {}

                    # Publish tool_call event
                    self._publish_stream_event(
                        task,
                        "tool_call",
                        {
                            "tool_name": tool_name,
                            "arguments": arguments,
                        },
                    )

                    self.logger.info(f"Tool call: {tool_name}({arguments})")

                    # Execute tool
                    tool_context = {"app": self.app, "user_id": user_id, "lang": lang}
                    tool_result = execute_tool(tool_name, arguments, tool_context)
                    last_tool_result = tool_result

                    self.logger.info(f"Tool result: {tool_result[:200]}")

                    # Publish tool_result event
                    result_preview = tool_result[:200] + "..." if len(tool_result) > 200 else tool_result
                    self._publish_stream_event(
                        task,
                        "tool_result",
                        {
                            "tool_name": tool_name,
                            "result_preview": result_preview,
                        },
                    )

                    # Add tool result to messages
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": tool_result,
                        }
                    )

                self.logger.info(
                    f"Tool calling iteration {iteration + 1}: "
                    f"executed {len(tool_calls)} tools: {[tc.get('function', {}).get('name', '?') for tc in tool_calls]}"
                )
                continue

            # No tool_calls — final content response
            full_response = response if isinstance(response, str) else str(response)
            break

        # Stream final response if not already streamed
        if stream and full_response and not streamed:
            if self._is_task_cancelled(task["id"]):
                self._publish_stream_event(task, "stream_cancelled")
            else:
                for i, char in enumerate(full_response):
                    if i % 20 == 0 and self._is_task_cancelled(task["id"]):
                        self._publish_stream_event(task, "stream_cancelled")
                        break
                    self._publish_stream_token(task, char)
                    if i % 20 == 19:
                        time.sleep(0.01)

        if not full_response.strip():
            if last_tool_result.strip():
                full_response = last_tool_result.strip()
            else:
                return self._build_error_response(
                    session_id,
                    self.app.modules["base"]._(
                        "No response from chat model. Try rephrasing your request.", lang
                    ),
                    round(time.time() - stream_start, 1),
                    lang,
                )

        process_time = round(time.time() - stream_start, 1)
        result = self._save_and_respond(
            session_id,
            full_response,
            self._get_model_name("chat") or "unknown",
            process_time,
            extra={"model_type": "chat"},
            response_style=response_style,
            user_id=user_id,
        )

        # Extract facts in background thread (CPU-only, no GPU lock)
        if full_response.strip() and len(full_response) > 20:
            threading.Thread(
                target=_extract_facts_bg,
                args=(self.app, query, full_response[:3000], session_id, user_id, lang),
                daemon=True,
            ).start()

        return result

    def _process_remember_task(
        self,
        task: dict[str, Any],
        query: str,
        current_time_str: str,
        session_id: str,
        user_id: str,
        lang: str,
        response_style: str = "neutral",
    ) -> dict[str, Any]:
        """Process explicit 'remember this' request via LLM and save to SLM."""
        from app.utils import format_prompt

        slm = self.app.modules.get("slm")
        if not slm or not slm.available:
            # SLM not available — just answer normally
            return self._process_chat_with_tools(
                task, query, current_time_str, session_id, user_id, lang, response_style,
            )

        # LLM processing to extract the essence of the request
        prompt = format_prompt("slm_remember.template", {"query": query}, lang=lang)
        if not prompt:
            return self._process_chat_with_tools(
                task, query, current_time_str, session_id, user_id, lang, response_style,
            )

        result = self.app.modules["base"].call_llamacpp(
            [{"role": "user", "content": prompt}],
            model_type="chat", lang=lang,
            temperature=0.1,
        )

        # Parse JSON and save to SLM
        facts = _parse_remember_json(result)
        for fact in facts:
            slm.remember(
                fact,
                metadata={
                    "session_id": session_id,
                    "fact_type": "general",
                    "category": "instruction",
                    "source": "user_request",
                },
                profile=user_id,
            )

        # Answer via chat model (confirmation)
        return self._process_chat_with_tools(
            task, query, current_time_str, session_id, user_id, lang, response_style,
        )

    def _process_fact_extraction(self, task: dict[str, Any]) -> dict[str, Any]:
        """Extract facts from Q&A via rule-based patterns (CPU-only, no GPU)."""
        try:
            request_data = task.get("data", {})
            query = request_data.get("query", "")
            response_text = request_data.get("response", "")
            session_id = request_data.get("session_id", task["session_id"])
            user_id = task["user_id"]
            lang = task.get("lang", "ru")

            slm = self.app.modules.get("slm")
            if not slm or not slm.available:
                return {"status": "ok"}

            from app.slm_extract import extract_facts_from_exchange

            existing = slm.list_facts(limit=50, profile=user_id)
            facts = extract_facts_from_exchange(query, response_text, existing, lang=lang)

            for fact in facts:
                # Semantic deduplication before saving
                try:
                    similarity = slm.check_similarity(fact["text"], profile=user_id)
                    if similarity >= 0.85:
                        continue
                except Exception:
                    pass

                slm.remember(
                    fact["text"],
                    metadata={
                        "session_id": session_id,
                        "fact_type": fact.get("fact_type", "general"),
                        "category": fact.get("category", "context"),
                        "source": "extraction",
                    },
                    profile=user_id,
                )

            return {"status": "ok", "facts_extracted": len(facts)}
        except Exception as e:
            self.app.logger.warning(f"Fact extraction failed: {e}")
            return {"status": "ok"}

    def _process_fact_merge(self, task: dict[str, Any]) -> dict[str, Any]:
        """Rule-based fact merging (background, CPU-only — no GPU lock)."""
        try:
            request_data = task.get("data", {})
            user_id = request_data.get("user_id", task["user_id"])
            lang = request_data.get("lang", "ru")

            slm = self.app.modules.get("slm")
            if not slm:
                return {"status": "ok"}
            if not slm.available:
                slm.check_availability()
            if not slm.available:
                return {"status": "ok"}

            from app.slm_merge import merge_facts_for_user
            merge_facts_for_user(slm, user_id, lang)

            return {"status": "ok"}
        except Exception as e:
            self.app.logger.warning(f"Fact merge failed: {e}")
            return {"status": "ok"}

    # Modified: removed hardcoded is_image_edit block; all image+text now go through _process_image_chat_task
    def _process_request(self, task: dict[str, Any]) -> dict[str, Any]:
        """Main entry point — delegates to specialized task handlers."""
        self.app.logger.info(f"RedisRequestQueue._process_request: processing task {task['id']}")
        self.app._last_task_time = time.time()  # Track for merge watcher

        task_type = task.get("type") or task.get("data", {}).get("type")
        self.app.logger.info(f"_process_request: task_type={task_type}, task_id={task.get('id')}")

        if task_type == "index_document":
            self.app.logger.info(f"_process_request: calling _process_index_task for task {task.get('id')}")
            return self._process_index_task(task)
        if task_type == "reindex_all_embeddings":
            return self._process_reindex_all_task(task)
        if task_type == "transcribe_audio":
            return self._process_transcribe_task(task)
        if task_type == "video":
            return self._process_video_request(task)
        if task_type == "image_gen":
            return self._process_image_gen_request(task)
        if task_type == "reasoning_task":
            return self._process_reasoning_request(task)
        if task_type == "fact_extraction_task":
            return self._process_fact_extraction(task)
        if task_type == "fact_merge_task":
            return self._process_fact_merge(task)

        user_id = task["user_id"]
        session_id = task["session_id"]
        request_data = task["data"]
        lang = task.get("lang", "ru")
        current_time_str = get_current_time_in_timezone(self.app) or get_current_time_in_timezone_for_db(self.app)
        response_style = request_data.get("response_style", "neutral")

        request_type = request_data.get("type", "text")
        message_text = request_data.get("text", "")
        file_data = request_data.get("file_data")
        file_type = request_data.get("file_type")
        file_name = request_data.get("file_name")

        # Audio files → dedicated handler
        if file_type and file_type.startswith("audio/"):
            return self._process_audio_task(task, request_data, session_id, user_id, lang)

        # Text request → router (streaming if requested)
        if request_type == "text":
            if request_data.get("stream", False):
                return self._process_text_task_stream(
                    task, message_text, session_id, user_id, current_time_str, lang, response_style
                )
            return self._process_text_task(message_text, session_id, user_id, current_time_str, lang, response_style)

        # Image + text chat (question about image or edit request)
        # The actual decision between analysis and editing is now made by the multimodal model
        if request_type == "image" and file_data:
            if request_data.get("stream", False):
                return self._process_image_chat_task_stream(
                    task,
                    file_data,
                    file_type or "",
                    file_name or "",
                    message_text,
                    session_id,
                    current_time_str,
                    lang,
                    user_id,
                    response_style,
                )
            return self._process_image_chat_task(
                file_data,
                file_type or "",
                file_name or "",
                message_text,
                session_id,
                current_time_str,
                lang,
                user_id,
                response_style,
                user_class=task.get("user_class", 2),
            )

        # Unknown request type
        return self._build_error_response(
            session_id, self.app.modules["base"]._("Unknown request type", lang=lang), 0, lang
        )

    # Modified: added handling of [-IMAGE-EDIT-] marker, and user_id parameter
    def _process_image_chat_task(
        self,
        file_data: str,
        file_type: str,
        file_name: str,
        message_text: str,
        session_id: str,
        current_time_str: str,
        lang: str,
        user_id: str,
        response_style: str = "neutral",
        user_class: int = 2,
    ) -> dict[str, Any]:
        """Handle image + text chat (user uploads image and asks question or requests edit).
        The multimodal model decides: analysis answer or edit marker."""
        process_start = time.time()
        is_error = False

        if "multimodal" not in self.app.modules or not self.app.modules["multimodal"].available:
            bot_reply = "⚠️ " + self.app.modules["base"]._("Multimodal model unavailable", lang)
            process_time = round(time.time() - process_start, 1)
            is_error = True
        else:
            file_size = int((len(file_data) * 3) / 4) if file_data else 0
            is_valid, error = self.app.modules["multimodal"].validate_image(file_data, file_type, file_name, file_size)
            if is_valid:
                self._unload_llamacpp_models()
                self._unload_video_pipeline()
                if not self._wait_for_vram(self._get_vram_needed("multimodal")):
                    bot_reply = "⚠️ " + self.app.modules["base"]._(
                        "GPU memory unavailable. Try again in a moment.", lang
                    )
                    process_time = round(time.time() - process_start, 1)
                    is_error = True
                else:
                    bot_reply, error = self.app.modules["multimodal"].process_image_with_text(
                        file_data,
                        message_text,
                        current_time_str,
                        lang=lang,
                        session_id=session_id,
                        response_style=response_style,
                    )
                process_time = round(time.time() - process_start, 1)
                if error:
                    bot_reply = f"⚠️ {error}"
                    is_error = True
                else:
                    # Check if the response indicates an image editing request
                    if isinstance(bot_reply, str) and bot_reply.strip().startswith("[-IMAGE-EDIT-]"):
                        edit_query = bot_reply.strip()[len("[-IMAGE-EDIT-]") :].strip()
                        if edit_query:
                            # Redirect to image editing task
                            return self._process_image_edit_task(
                                edit_query, file_data, file_type, session_id, user_id, lang, response_style
                            )
                        else:
                            # Marker present but no query, treat as error
                            bot_reply = "⚠️ " + self.app.modules["base"]._("Image editing request was empty", lang)
                            is_error = True
                    # Check if the response indicates a video generation request
                    elif isinstance(bot_reply, str) and bot_reply.strip().startswith("[-VIDEO-]"):
                        video_query = bot_reply.strip()[len("[-VIDEO-]") :].strip()
                        if video_query:
                            return self._requeue_video_task(
                                video_query,
                                session_id,
                                user_id,
                                lang,
                                response_style,
                                file_data=file_data,
                                file_type=file_type,
                                file_name=file_name,
                                user_class=user_class,
                            )
                        else:
                            bot_reply = "⚠️ " + self.app.modules["base"]._("Video request was empty", lang)
                            is_error = True
                    # Safety net: if model returned edit-like content without the required marker,
                    # treat as a model error, NOT an edit request.
                    elif isinstance(bot_reply, str) and "edit_prompt" in bot_reply:
                        self.app.logger.warning(
                            f"Multimodal model returned 'edit_prompt' without [-IMAGE-EDIT-] marker. "
                            f"Treating as classification error. Response prefix: {bot_reply[:100]}..."
                        )
                        bot_reply = "⚠️ " + self.app.modules["base"]._(
                            "Failed to process image request. Please try again.", lang
                        )
                        is_error = True
            else:
                bot_reply = "⚠️ " + (error or self.app.modules["base"]._("Invalid image", lang))
                process_time = round(time.time() - process_start, 1)
                is_error = True

        mm_model = "system" if is_error else (self._get_model_name("multimodal") or "unknown")
        model_type = "system" if is_error else "multimodal"
        return self._save_and_respond(
            session_id,
            bot_reply,
            mm_model,
            process_time,
            is_error=is_error,
            extra={"model_type": model_type},
            response_style=response_style,
        )

    def _process_image_chat_task_stream(
        self,
        task: dict[str, Any],
        file_data: str,
        file_type: str,
        file_name: str,
        message_text: str,
        session_id: str,
        current_time_str: str,
        lang: str,
        user_id: str,
        response_style: str = "neutral",
    ) -> dict[str, Any]:
        """Handle image+text with streaming. Buffers early tokens to detect [-IMAGE-EDIT-] or [-VIDEO-] marker."""
        edit_marker = "[-IMAGE-EDIT-]"
        video_marker = "[-VIDEO-]"
        process_start = time.time()

        if "multimodal" not in self.app.modules or not self.app.modules["multimodal"].available:
            bot_reply = "⚠️ " + self.app.modules["base"]._("Multimodal model unavailable", lang)
            process_time = round(time.time() - process_start, 1)
            return self._save_and_respond(
                session_id,
                bot_reply,
                "system",
                process_time,
                is_error=True,
                extra={"model_type": "system"},
                response_style=response_style,
            )

        file_size = int((len(file_data) * 3) / 4) if file_data else 0
        is_valid, error = self.app.modules["multimodal"].validate_image(file_data, file_type, file_name, file_size)
        if not is_valid:
            bot_reply = "⚠️ " + (error or self.app.modules["base"]._("Invalid image", lang))
            process_time = round(time.time() - process_start, 1)
            return self._save_and_respond(
                session_id,
                bot_reply,
                "system",
                process_time,
                is_error=True,
                extra={"model_type": "system"},
                response_style=response_style,
            )

        self._unload_llamacpp_models()
        self._unload_video_pipeline()
        if not self._wait_for_vram(self._get_vram_needed("multimodal")):
            bot_reply = "⚠️ " + self.app.modules["base"]._("GPU memory unavailable. Try again in a moment.", lang)
            process_time = round(time.time() - process_start, 1)
            return self._save_and_respond(
                session_id,
                bot_reply,
                "system",
                process_time,
                is_error=True,
                extra={"model_type": "system"},
                response_style=response_style,
            )
        stream_gen = self.app.modules["multimodal"].process_image_with_text_stream(
            file_data,
            message_text,
            current_time_str,
            lang=lang,
            session_id=session_id,
            response_style=response_style,
        )

        full_response = ""
        cancelled = False

        # No text → no edit possible, stream directly
        if not message_text.strip():
            full_response = ""
            error_detected = False
            for token in stream_gen:
                full_response += token
                # Don't publish error tokens — they need the "⚠️ " prefix added by
                # _build_error_response. Mirrors _process_camera_task_stream.
                if not error_detected:
                    if self._is_llm_error_string(full_response):
                        error_detected = True
                    else:
                        self._publish_stream_token(task, token)
                if self._is_task_cancelled(task["id"]):
                    cancelled = True
                    break
            process_time = round(time.time() - process_start, 1)
            mm_model = self._get_model_name("multimodal") or "unknown"
            if cancelled:
                self.app.logger.info(f"Task {task['id']} cancelled during image chat stream")
                self._publish_stream_event(task, "stream_cancelled")
            if error_detected or self._is_llm_error_string(full_response):
                return self._build_error_response(session_id, full_response, process_time, lang)
            return self._save_and_respond(
                session_id,
                full_response,
                mm_model,
                process_time,
                extra={"model_type": "multimodal"},
                response_style=response_style,
            )

        # Text present → buffer early tokens to detect edit marker
        buffer = ""
        released = False
        for token in stream_gen:
            full_response += token
            if not released:
                buffer += token
                if edit_marker in buffer:
                    for token in stream_gen:
                        full_response += token
                    edit_query = full_response.split(edit_marker, 1)[1].strip()
                    if edit_query:
                        return self._process_image_edit_task(
                            edit_query,
                            file_data,
                            file_type,
                            session_id,
                            user_id,
                            lang,
                            response_style,
                            task=task,
                        )
                    bot_reply = "⚠️ " + self.app.modules["base"]._("Image editing request was empty", lang)
                    process_time = round(time.time() - process_start, 1)
                    return self._save_and_respond(
                        session_id,
                        bot_reply,
                        "unknown",
                        process_time,
                        is_error=True,
                        extra={"model_type": "system"},
                        response_style=response_style,
                    )
                if video_marker in buffer:
                    for token in stream_gen:
                        full_response += token
                    video_query = full_response.split(video_marker, 1)[1].strip()
                    if video_query:
                        return self._requeue_video_task(
                            video_query,
                            session_id,
                            user_id,
                            lang,
                            response_style,
                            file_data=file_data,
                            file_type=file_type,
                            file_name=file_name,
                            user_class=task.get("user_class", 2),
                        )
                    bot_reply = "⚠️ " + self.app.modules["base"]._("Video request was empty", lang)
                    process_time = round(time.time() - process_start, 1)
                    return self._save_and_respond(
                        session_id,
                        bot_reply,
                        "unknown",
                        process_time,
                        is_error=True,
                        extra={"model_type": "system"},
                        response_style=response_style,
                    )
                if len(buffer) > 100:
                    self._publish_stream_token(task, buffer)
                    released = True
                    buffer = ""
            else:
                self._publish_stream_token(task, token)

            if self._is_task_cancelled(task["id"]):
                cancelled = True
                break

        if buffer:
            self._publish_stream_token(task, buffer)

        process_time = round(time.time() - process_start, 1)
        mm_model = self._get_model_name("multimodal") or "unknown"
        if cancelled:
            self.app.logger.info(f"Task {task['id']} cancelled during image chat stream")
            self._publish_stream_event(task, "stream_cancelled")
        if self._is_llm_error_string(full_response):
            return self._build_error_response(session_id, full_response, process_time, lang)
        return self._save_and_respond(
            session_id,
            full_response,
            mm_model,
            process_time,
            extra={"model_type": "multimodal"},
            response_style=response_style,
        )

    def _process_audio_task(
        self, task: dict[str, Any], request_data: dict[str, Any], session_id: str, user_id: str, lang: str
    ) -> dict[str, Any]:
        """Process audio file (voice message or audio upload) via transcription."""
        file_data = request_data.get("file_data")
        file_type = request_data.get("file_type")
        file_name = request_data.get("file_name")
        voice_record = request_data.get("voice_record", False)
        user_class = task.get("user_class", 2)

        process_start_time = time.time()

        audio_module = self.app.modules.get("audio")
        if not audio_module:
            process_time = round(time.time() - process_start_time, 1)
            error_msg = "⚠️ " + self.app.modules["base"]._("Audio service unavailable", lang)
            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            message_id = save_message(
                session_id, "assistant", error_msg, model_name="system", response_time=str(process_time)
            )
            return {
                "response": error_msg,
                "session_id": session_id,
                "model_used": "system",
                "assistant_timestamp": completion_time_for_db,
                "response_time": process_time,
                "is_error": True,
                "message_id": message_id,
            }

        transcribed_text = audio_module.transcribe(file_data, file_type, file_name, lang=lang)
        process_time = round(time.time() - process_start_time, 1)

        if transcribed_text is None:
            error_msg = "⚠️ " + self.app.modules["base"]._("Failed to recognize speech", lang)
            completion_time_for_db = get_current_time_in_timezone_for_db(self.app)
            message_id = save_message(
                session_id, "assistant", error_msg, model_name="system", response_time=str(process_time)
            )
            return {
                "response": error_msg,
                "session_id": session_id,
                "model_used": "system",
                "assistant_timestamp": completion_time_for_db,
                "response_time": process_time,
                "is_error": True,
                "message_id": message_id,
            }

        from flask_babel import force_locale

        with force_locale(lang):
            prefix = "🎤 " + self.app.modules["base"]._("Transcribed") + ": "

        system_content = json.dumps({"prefix": prefix, "text": transcribed_text}, ensure_ascii=False)

        transcribed_message_id = save_message(
            session_id, "assistant", system_content, model_name="whisper", response_time=str(process_time)
        )

        if voice_record:
            response_style = request_data.get("response_style", "neutral")
            text_request_data = {
                "type": "text",
                "text": transcribed_text,
                "preview": (transcribed_text[:50] + "...")
                if transcribed_text
                else self.app.modules["base"]._("Voice request", lang),
                "response_style": response_style,
            }
            new_request_id, _ = self.add_request(user_id, session_id, text_request_data, user_class, lang=lang)

            return {
                "transcribed_text": transcribed_text,
                "transcribed_message_id": transcribed_message_id,
                "request_id": new_request_id,
                "session_id": session_id,
                "response_time": process_time,
            }
        else:
            return {
                "transcribed_text": transcribed_text,
                "transcribed_message_id": transcribed_message_id,
                "session_id": session_id,
                "response_time": process_time,
            }

    def _process_transcribe_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Process audio transcription task asynchronously."""
        user_id = task["user_id"]
        session_id = task["session_id"]
        request_data = task["data"]
        file_data = request_data.get("file_data")
        file_type = request_data.get("file_type")
        file_name = request_data.get("file_name")
        voice_record = request_data.get("voice_record", False)
        lang = task.get("lang", "ru")
        user_class = task.get("user_class", 2)

        audio_module = self.app.modules.get("audio")
        if not audio_module:
            error_msg = self.app.modules["base"]._("Audio service unavailable", lang)
            save_message(session_id, "assistant", "⚠️ " + error_msg, model_name="system", response_time="0")
            return {"error": error_msg, "session_id": session_id, "is_error": True}

        start_time = time.time()
        transcribed_text = audio_module.transcribe(file_data, file_type, file_name, lang=lang)
        process_time = round(time.time() - start_time, 1)
        if transcribed_text is None:
            error_msg = self.app.modules["base"]._("Failed to recognize speech", lang)
            save_message(session_id, "assistant", "⚠️ " + error_msg, model_name="system", response_time="0")
            return {"error": error_msg, "session_id": session_id, "is_error": True}

        from flask_babel import force_locale

        with force_locale(lang):
            prefix = "🎤 " + self.app.modules["base"]._("Transcribed") + ": "

        system_content = json.dumps({"prefix": prefix, "text": transcribed_text}, ensure_ascii=False)
        transcribed_message_id = save_message(
            session_id, "assistant", system_content, model_name="whisper", response_time=str(process_time)
        )

        if voice_record:
            response_style = request_data.get("response_style", "neutral")
            image_data = request_data.get("image_data")
            image_type = request_data.get("image_type")
            image_name = request_data.get("image_name")

            if image_data:
                # Image + voice: requeue as image task (multimodal processing)
                requeue_request_data = {
                    "type": "image",
                    "text": transcribed_text,
                    "file_data": image_data,
                    "file_type": image_type,
                    "file_name": image_name,
                    "preview": (transcribed_text[:50] + "...")
                    if transcribed_text
                    else self.app.modules["base"]._("Voice request", lang=lang),
                    "response_style": response_style,
                    "stream": True,
                }
            else:
                # Voice only: requeue as text task (router processing)
                requeue_request_data = {
                    "type": "text",
                    "text": transcribed_text,
                    "preview": (transcribed_text[:50] + "...")
                    if transcribed_text
                    else self.app.modules["base"]._("Voice request", lang=lang),
                    "response_style": response_style,
                    "stream": True,
                }
            new_request_id, _ = self.app.request_queue.add_request(
                user_id, session_id, requeue_request_data, user_class, lang=lang
            )
            return {
                "transcribed_text": transcribed_text,
                "transcribed_message_id": transcribed_message_id,
                "request_id": new_request_id,
                "session_id": session_id,
                "response_time": process_time,
                "assistant_timestamp": get_current_time_in_timezone_for_db(self.app),
            }
        else:
            return {
                "transcribed_text": transcribed_text,
                "transcribed_message_id": transcribed_message_id,
                "session_id": session_id,
                "response_time": process_time,
                "assistant_timestamp": get_current_time_in_timezone_for_db(self.app),
            }

    def _publish_document_event(self, user_id: str, doc_id: str, index_status: str) -> None:
        """Publish a document_indexed event to the user's SSE stream."""
        publisher = get_events_publisher()
        if publisher is None:
            return
        publisher.publish(
            user_id,
            "document_indexed",
            {
                "doc_id": doc_id,
                "index_status": index_status,
            },
        )

    def _process_index_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Index a document and store embeddings in Qdrant."""
        task_id = task.get("id", "unknown")
        self.app.logger.info(f"_process_index_task: STARTING for task {task_id}")

        data = task.get("data", {})
        doc_id = data.get("doc_id")
        file_path = data.get("file_path")
        user_id = task["user_id"]
        indexing_started_at = get_current_time_for_db()
        update_document_index_status(doc_id, INDEX_STATUS_INDEXING, indexing_started_at=indexing_started_at)
        rag = self.app.modules.get("rag")
        if not rag or not rag.available:
            with force_locale("en"):
                error_msg = self.app.modules["base"]._("RAG module unavailable")
            update_document_index_status(doc_id, INDEX_STATUS_FAILED)
            self._publish_document_event(user_id, doc_id, INDEX_STATUS_FAILED)
            return {"success": False, "error": error_msg, "doc_id": doc_id}
        try:
            success, message = rag.index_document(user_id, doc_id, file_path)
            if success:
                indexed_at = get_current_time_for_db()
                embedding_model = self._get_model_name("embedding") or "unknown"
                update_document_index_status(
                    doc_id,
                    INDEX_STATUS_INDEXED,
                    indexed_at=indexed_at,
                    indexing_started_at=indexing_started_at,
                    embedding_model=embedding_model,
                )
                self._publish_document_event(user_id, doc_id, INDEX_STATUS_INDEXED)
                self.app.logger.info(f"Set embedding_model for doc {doc_id} to {embedding_model}")
                return {"success": True, "message": message, "doc_id": doc_id}
            else:
                update_document_index_status(doc_id, INDEX_STATUS_FAILED)
                self._publish_document_event(user_id, doc_id, INDEX_STATUS_FAILED)
                return {"success": False, "error": message, "doc_id": doc_id}
        except Exception as e:
            self.app.logger.error(f"Indexing failed for doc {doc_id}: {e}")
            update_document_index_status(doc_id, INDEX_STATUS_FAILED)
            self._publish_document_event(user_id, doc_id, INDEX_STATUS_FAILED)
            return {"success": False, "error": str(e), "doc_id": doc_id}

    def _process_reindex_all_task(self, task: dict[str, Any]) -> dict[str, Any]:
        self.app.logger.info("Starting reindex of all documents with new embedding model.")
        from app.database import get_db

        from .db import get_current_time_for_db

        rag = self.app.modules.get("rag")
        if not rag or not rag.available:
            self.app.logger.error("RAG module not available for reindexing")
            with force_locale("en"):
                error_msg = self.app.modules["base"]._("RAG module unavailable")
            return {"success": False, "error": error_msg}

        batch_size = 50
        offset = 0
        total = 0
        success_count = 0
        fail_count = 0
        all_doc_ids = []

        while True:
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT id, user_id, file_path FROM documents LIMIT %s OFFSET %s", (batch_size, offset))
                documents = c.fetchall()

            if not documents:
                break

            total += len(documents)
            doc_ids = [doc["id"] for doc in documents]
            all_doc_ids.extend(doc_ids)

            placeholders = ",".join(["%s"] * len(doc_ids))
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    f"""
                    UPDATE documents
                    SET index_status = %s, indexed_at = NULL, indexing_started_at = NULL, embedding_model = NULL
                    WHERE id IN ({placeholders})
                """,
                    [INDEX_STATUS_PENDING] + doc_ids,
                )

            self.app.logger.info(f"Reindexing batch of {len(documents)} documents (offset={offset})")

            for doc in documents:
                doc_id = doc["id"]
                user_id = doc["user_id"]
                file_path = doc["file_path"]
                documents_folder = self.app.config["DOCUMENTS_FOLDER"]
                full_path = os.path.join(documents_folder, file_path)

                if not os.path.exists(full_path):
                    self.app.logger.warning(f"Document file not found: {full_path}, skipping")
                    update_document_index_status(doc_id, INDEX_STATUS_FAILED)
                    self._publish_document_event(user_id, doc_id, INDEX_STATUS_FAILED)
                    fail_count += 1
                    continue

                try:
                    rag.delete_document(doc_id, user_id)
                except Exception as e:
                    self.app.logger.error(f"Failed to delete old vectors for doc {doc_id}: {e}")

                try:
                    indexing_started_at = get_current_time_for_db()
                    update_document_index_status(doc_id, INDEX_STATUS_INDEXING, indexing_started_at=indexing_started_at)
                    success, message = rag.index_document(user_id, doc_id, full_path)
                    if success:
                        indexed_at = get_current_time_for_db()
                        embedding_model = self._get_model_name("embedding") or "unknown"
                        update_document_index_status(
                            doc_id, INDEX_STATUS_INDEXED, indexed_at=indexed_at, embedding_model=embedding_model
                        )
                        self._publish_document_event(user_id, doc_id, INDEX_STATUS_INDEXED)
                        success_count += 1
                    else:
                        update_document_index_status(doc_id, INDEX_STATUS_FAILED)
                        self._publish_document_event(user_id, doc_id, INDEX_STATUS_FAILED)
                        fail_count += 1
                        self.app.logger.error(f"Reindex failed for doc {doc_id}: {message}")
                except Exception as e:
                    self.app.logger.error(f"Reindex error for doc {doc_id}: {e}")
                    update_document_index_status(doc_id, INDEX_STATUS_FAILED)
                    self._publish_document_event(user_id, doc_id, INDEX_STATUS_FAILED)
                    fail_count += 1

            offset += batch_size

        self.app.logger.info(f"Reindex complete: total={total}, success={success_count}, failed={fail_count}")
        return {"success": True, "total": total, "success_count": success_count, "failed_count": fail_count}

    # Task types that run in the background and are invisible to the user.
    # They should not trigger ⚡ or ⏳ indicators in the UI.
    _BACKGROUND_TASK_TYPES = frozenset({"fact_extraction_task", "fact_merge_task"})

    def get_user_requests_status(self, user_id: str, lang: str = "ru") -> dict[str, Any]:
        """Get status of user's requests (processing, queued, completed).

        Collects ALL tasks from both fast and slow processing queues.
        The first one is returned as ``processing`` (⚡), the rest are
        added to ``queued`` (⏳) so no active task is invisible.
        Background tasks (fact extraction, fact merge) are excluded.
        """
        result: dict = {"processing": None, "queued": [], "recent_completed": []}
        processing_session_ids: set[str] = set()

        # Collect ALL processing tasks from both queues
        all_processing: list[dict] = []
        for proc_key in [self.processing_key, self.slow_processing_key]:
            processing_tasks = self.redis.hgetall(proc_key)
            for req_id, task_data in processing_tasks.items():
                req_id = req_id.decode() if isinstance(req_id, bytes) else req_id
                task = self._deserialize(task_data)
                if task and task.get("user_id") == user_id:
                    if task.get("type") in self._BACKGROUND_TASK_TYPES:
                        continue
                    task["status"] = "processing"
                    task["position_info"] = {"position": 1, "estimated_seconds": 0}
                    all_processing.append(task)
                    sid = task.get("session_id")
                    if sid:
                        processing_session_ids.add(sid)

        # First → ⚡, rest → ⏳ (shown as queued but are actually processing)
        if all_processing:
            result["processing"] = self._format_request_info(all_processing[0], lang)
            for task in all_processing[1:]:
                task["status"] = "queued"
                task["position_info"] = {"position": 0, "estimated_seconds": 0}
                result["queued"].append(self._format_request_info(task, lang))

        # Enrich processing entry with current stage from Redis progress hash
        if result["processing"]:
            task_id = result["processing"].get("id")
            if task_id:
                try:
                    stage = self.redis.hget(f"task_progress:{task_id}", "stage")
                    if stage:
                        result["processing"]["stage"] = stage
                except Exception:
                    pass

        # Collect items actually waiting in queues
        position = 1
        for q_key in [self.queue_key, self.slow_queue_key]:
            queue_length = self.redis.llen(q_key)
            queue_tasks = self.redis.lrange(q_key, 0, queue_length - 1) if queue_length > 0 else []
            for task_data in queue_tasks:
                task = self._deserialize(task_data)
                if task and task.get("user_id") == user_id:
                    if task.get("type") in self._BACKGROUND_TASK_TYPES:
                        continue
                    if task.get("session_id") in processing_session_ids:
                        continue
                    task["status"] = "queued"
                    task["position_info"] = {"position": position, "estimated_seconds": max(1, position * 5)}
                    result["queued"].append(self._format_request_info(task, lang))
                    position += 1

        return result

    def _format_request_info(self, task: dict[str, Any], lang: str = "ru") -> dict[str, Any]:
        type_icons = {
            "text": "💬",
            "image": "🎨",
            "camera": "📷",
            "reasoning": "🧠",
            "audio": "🎤",
            "index_document": "📄",
            "transcribe_audio": "🎤",
        }
        return {
            "id": task["id"],
            "session_id": task.get("session_id"),
            "session_title": task.get("session_title", self.app.modules["base"]._("Unknown session", lang=lang)),
            "type": task.get("data", {}).get("type", task.get("type", "unknown")),
            "type_icon": type_icons.get(task.get("data", {}).get("type", task.get("type", "unknown")), "📄"),
            "status": task.get("status", "queued"),
            "position_info": task.get("position_info", {"position": 0, "estimated_seconds": 0}),
            "preview": task.get("data", {}).get("preview", ""),
        }

    def check_result(self, request_id: str) -> dict[str, Any] | None:
        """Check if result is available for a request."""
        result_data = self.redis.hget(self.results_key, request_id)
        if result_data:
            return self._deserialize(result_data)
        return None

    def _publish_stream_token(self, task: dict[str, Any], token: str) -> None:
        """Publish a single stream token to the user's SSE event stream."""
        user_id = task.get("user_id")
        if not user_id or not token:
            return
        publisher = get_events_publisher()
        if publisher is None:
            return
        publisher.publish(
            user_id,
            "stream_token",
            {
                "task_id": task.get("id"),
                "session_id": task.get("session_id"),
                "token": token,
            },
        )

    def _save_progress(self, task_id: str, progress_type: str, data: dict[str, Any]) -> None:
        """Persist latest progress state in Redis for restore after reconnect/page reload."""
        key = f"task_progress:{task_id}"
        mapping = {"type": progress_type, "timestamp": str(time.time())}
        if progress_type == "task_progress":
            mapping["stage"] = data.get("stage", "")
        elif progress_type in ("video_step", "image_step"):
            mapping["step"] = str(data.get("step", 0))
            mapping["total"] = str(data.get("total", 0))
            mapping["percent"] = str(data.get("percent", 0))
        try:
            pipe = self.redis.pipeline()
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, 1800)
            pipe.execute()
        except Exception:
            pass  # non-critical

    def _cleanup_progress(self, task_id: str) -> None:
        """Remove progress hash when task completes."""
        with contextlib.suppress(Exception):
            self.redis.delete(f"task_progress:{task_id}")

    def _publish_stream_event(self, task: dict[str, Any], event_type: str, extra: dict | None = None) -> None:
        """Publish a streaming lifecycle event (e.g. stream_cancelled)."""
        user_id = task.get("user_id")
        if not user_id:
            return
        publisher = get_events_publisher()
        if publisher is None:
            return
        payload = {
            "task_id": task.get("id"),
            "session_id": task.get("session_id"),
        }
        if extra:
            payload.update(extra)
        publisher.publish(user_id, event_type, payload)
        if event_type == "task_progress" and extra:
            self._save_progress(task.get("id", ""), "task_progress", extra)

    def _publish_result_event(self, task: dict[str, Any], status: str, result_data: dict[str, Any]) -> None:
        """Publish task result to the user's SSE event stream."""
        user_id = task.get("user_id")
        if not user_id:
            return
        publisher = get_events_publisher()
        if publisher is None:
            return
        publisher.publish(
            user_id,
            "result_completed",
            {
                "task_id": task.get("id"),
                "session_id": task.get("session_id"),
                "status": status,
                "result": result_data,
            },
        )
        self._cleanup_progress(task.get("id", ""))

    def cancel_task(self, task_id: str) -> bool:
        """Mark a task as cancelled in Redis. Returns True if the task exists."""
        exists = self.redis.hexists(self.processing_key, task_id)
        if not exists:
            exists = self.redis.hexists(self.slow_processing_key, task_id)
        if not exists:
            exists = self.redis.hexists(self.results_key, task_id)
        if not exists:
            return False
        ttl = self.app.config.get("QUEUE_MAX_WAIT_TIME", 300) + 120
        self.redis.setex(f"task:cancel:{task_id}", ttl, "1")
        self.app.logger.info(f"Task {task_id} marked as cancelled")
        return True

    @staticmethod
    def _try_parse_text_tool_call(text: str) -> dict[str, Any] | None:
        """Try to parse a raw JSON tool call from model text output.

        Small models sometimes output tool calls as JSON text instead of
        using the structured tool_calls API. Detect and parse these.
        Supports:
        1. JSON with "name"/"arguments" keys (standard format)
        2. "tool_name {args_json}" — tool name as prefix, JSON is arguments
        3. ```json blocks and <tool_call> tags
        """
        known_tool_names = {
            "get_current_time",
            "calculator",
            "web_search",
            "rag_search",
            "camera_snapshot",
            "time_calc",
        }
        text = text.strip()

        # Try <tool_call>...</tool_call> format
        tc_match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)
        if tc_match:
            text = tc_match.group(1).strip()

        # Try ```json...``` code block
        if "```" in text:
            code_match = re.search(r"```(?:json)?\s*\n?\s*(\{.*?\})\s*\n?\s*```", text, re.DOTALL)
            if code_match:
                text = code_match.group(1).strip()

        # Find JSON object { ... }
        idx = text.find("{")
        if idx < 0:
            return None

        json_text = text[idx:]

        # Find balanced closing brace
        depth = 0
        end = -1
        for i, ch in enumerate(json_text):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return None
        json_text = json_text[: end + 1]

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            return None

        # Format 1: JSON has "name" and "arguments" keys
        if "name" in data and "arguments" in data:
            name = data["name"]
            args = data["arguments"]
            if not isinstance(args, dict):
                return None
            return {
                "id": f"text_{name}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }

        # Format 2: text before { contains a known tool name, JSON is args
        # e.g. 'time_calc {"operation": "days_until_end_of", "period": "month"}'
        prefix = text[:idx].strip().split()[-1] if text[:idx].strip() else ""
        if prefix in known_tool_names and isinstance(data, dict):
            return {
                "id": f"text_{prefix}",
                "type": "function",
                "function": {
                    "name": prefix,
                    "arguments": json.dumps(data, ensure_ascii=False),
                },
            }

        return None

    def _is_task_cancelled(self, task_id: str) -> bool:
        """Check if a task has been cancelled (polled by the streaming worker)."""
        return bool(self.redis.exists(f"task:cancel:{task_id}"))

    def _start_cancel_checker(self, task_id: str, interval: float = 2.0) -> "threading.Event":
        """Start a background thread that polls _is_task_cancelled and restarts
        the LTX-Video container when cancellation is detected.

        Returns a threading.Event that can be set() to stop the checker.
        """
        stop_event = threading.Event()

        def _checker():
            while not stop_event.is_set():
                stop_event.wait(interval)
                if stop_event.is_set():
                    break
                if self._is_task_cancelled(task_id):
                    self.logger.info(f"Cancelling task {task_id} — restarting LTX-Video container")
                    try:
                        from app.resource_manager import get_resource_manager
                        get_resource_manager()._force_restart_ltx_video()
                    except Exception as e:
                        self.logger.debug(f"Container restart during cancel: {e}")
                    return

        t = threading.Thread(target=_checker, daemon=True)
        t.start()
        return stop_event
