# app/queue.py
import hashlib
import hmac
import json
import os
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
from .utils import (
    estimate_tokens,
    get_current_time_in_timezone,
    get_current_time_in_timezone_for_db,
    save_uploaded_file,
)


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
        self.processing_key = "processing_requests"
        self.slow_processing_key = "slow_processing_requests"
        self.results_key = "request_results"
        self.user_requests_key = "user_requests"
        # HMAC key for signing serialized data (prevent tampering)
        self.hmac_key = app.config.get("SECRET_KEY", "fallback-key").encode("utf-8")
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
        # Text tasks are fast
        if req_type == "text":
            return "fast"
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
        queue_key = self.slow_queue_key if queue_type == "slow" else self.queue_key

        serialized = self._serialize(task)
        self.redis.rpush(queue_key, serialized)

        # Track per-user queue count
        self._increment_user_queue_count(user_id)

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
        """Get user's queue count and total queue length in O(1)."""
        fast_total = self.redis.llen(self.queue_key)
        slow_total = self.redis.llen(self.slow_queue_key)
        total = fast_total + slow_total
        if total == 0:
            return 0, 0

        user_count_key = f"{self.queue_key}:user_counts"
        user_count = self.redis.hget(user_count_key, user_id)
        user_count = int(user_count) if user_count else 0

        total_count = self.redis.hget(user_count_key, "__total__")
        total_count = int(total_count) if total_count else 0
        if total_count != total:
            self.redis.delete(user_count_key)
            user_count = 0

        return user_count, total

    def _increment_user_queue_count(self, user_id: str):
        """Increment user's queue count (O(1))."""
        user_count_key = f"{self.queue_key}:user_counts"
        pipe = self.redis.pipeline()
        pipe.hincrby(user_count_key, user_id, 1)
        pipe.hincrby(user_count_key, "__total__", 1)
        pipe.execute()

    def _decrement_user_queue_count(self, user_id: str):
        """Decrement user's queue count (O(1))."""
        user_count_key = f"{self.queue_key}:user_counts"
        pipe = self.redis.pipeline()
        count = self.redis.hget(user_count_key, user_id)
        if count and int(count) > 0:
            pipe.hincrby(user_count_key, user_id, -1)
        else:
            pipe.hdel(user_count_key, user_id)
        pipe.hincrby(user_count_key, "__total__", -1)
        pipe.execute()

    def _cleanup_user_request(self, user_id: str, request_id: str):
        """Remove request ID from user's set after completion."""
        self.redis.srem(f"{self.user_requests_key}:{user_id}", request_id)

    def _recover_stale_tasks(self):
        """Recover tasks stuck in 'processing' state from a previous crash."""
        for queue_key, processing_key in [
            (self.queue_key, self.processing_key),
            (self.slow_queue_key, self.slow_processing_key),
        ]:
            try:
                processing_tasks = self.redis.hgetall(processing_key)
                if not processing_tasks:
                    continue

                recovered = 0
                for task_id_b, task_data_b in processing_tasks.items():
                    task_id = task_id_b.decode() if isinstance(task_id_b, bytes) else task_id_b
                    task_data = task_data_b.decode() if isinstance(task_data_b, bytes) else task_data_b

                    task = self._deserialize(task_data)
                    if task is None:
                        self.logger.warning(f"Recovery: corrupted task {task_id}, removing")
                        self.redis.hdel(processing_key, task_id)
                        continue

                    self.redis.rpush(queue_key, task_data)
                    self.redis.hdel(processing_key, task_id)
                    recovered += 1
                    self.logger.info(f"Recovery: re-queued stale task {task_id}")

                if recovered > 0:
                    self.app.logger.info(f"Queue recovery ({queue_key}): re-queued {recovered} stale task(s)")
            except Exception as e:
                self.logger.warning(f"Queue recovery failed for {queue_key}: {e}")

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

        if file_type and file_type.startswith("audio/"):
            return "chat"

        if req_type == "image" and file_type and file_type.startswith("image/"):
            return "multimodal"

        if req_type == "text":
            return "chat"

        return "chat"

    def _peek_next_task_model(self) -> tuple[str, bool]:
        """Peek at the next task in both queues and determine what model it needs."""
        for q_key in [self.queue_key, self.slow_queue_key]:
            queue_len = self.redis.llen(q_key)
            if queue_len > 0:
                task_data = self.redis.lindex(q_key, 0)
                if task_data:
                    task = self._deserialize(task_data)
                    if task:
                        return self._get_model_for_task(task), True
        return "none", False

    def _get_current_loaded_model(self) -> str | None:
        """Query llama.cpp to find which model is currently loaded in VRAM."""
        llamacpp_url = self.app.config.get("LLAMACPP_URL")
        if not llamacpp_url:
            return None

        try:
            import requests as req

            resp = req.get(f"{llamacpp_url.rstrip('/')}/v1/models", timeout=5)
            if resp.status_code != 200:
                return None

            data = resp.json()
            for model in data.get("data", []):
                if model.get("status", {}).get("value") == "loaded":
                    model_id = model.get("id", "")
                    from .model_config import get_model_config

                    for module_type in ("chat", "reasoning", "multimodal", "embedding"):
                        config = get_model_config(module_type)
                        if config and config.get("model_name") in model_id:
                            return module_type
                    if any(x in model_id.lower() for x in ("vl", "vision", "multimodal")):
                        return "multimodal"
                    if any(x in model_id.lower() for x in ("oss", "reason", "gemma-4")):
                        return "reasoning"
                    if any(x in model_id.lower() for x in ("bge", "embed")):
                        return "embedding"
                    return "chat"
            return None
        except Exception as e:
            self.app.logger.debug(f"Failed to query current model: {e}")
            return None

    def _predictive_unload(self, current_model: str) -> None:
        """After task completion, check next queued task and decide whether to unload."""
        cold_models = {"reasoning", "embedding"}

        if current_model == "none":
            return

        actual_model = self._get_current_loaded_model()
        if actual_model is None:
            self.app.logger.debug("Predictive unload: cannot determine current model, skipping")
            return

        next_model, has_tasks = self._peek_next_task_model()

        if actual_model == next_model:
            self.app.logger.info(
                f"Predictive unload: keeping '{actual_model}' in VRAM — next task also needs '{next_model}'"
            )
            return

        if not has_tasks:
            if actual_model in cold_models:
                self.app.logger.info(
                    f"Predictive unload: queue empty, '{actual_model}' is cold — unloading to free VRAM"
                )
            else:
                self.app.logger.info(
                    f"Predictive unload: queue empty, '{actual_model}' is hot — "
                    f"keeping loaded (may be needed for new request)"
                )
                return

        self.app.logger.info(
            f"Predictive unload: current='{actual_model}', next='{next_model}' — unloading '{actual_model}'"
        )

        llamacpp_url = self.app.config.get("LLAMACPP_URL")
        if not llamacpp_url:
            return

        from .resource_manager import get_resource_manager

        rm = get_resource_manager()
        if rm:
            rm.unload_llamacpp_model(llamacpp_url)
            if has_tasks:
                self.app.logger.info(f"VRAM freed — llama.cpp will auto-load '{next_model}' for next task")
        else:
            self.app.logger.debug("Resource manager not available, skipping unload")

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

        try:
            with self.app.app_context():
                result_data = self._process_request(task)
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
                self._decrement_user_queue_count(user_id)

        current_model = self._get_model_for_task(task)
        self._predictive_unload(current_model)

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

                self._process_single_task(task, self.processing_key)
            except Exception as e:
                self.logger.error(f"Fast worker error: {e}")
                time.sleep(1)
        self.app.logger.info("Fast worker stopped gracefully")

    def _worker_loop_slow(self):
        """Worker for slow queue (image generation/editing)."""
        self.app.logger.info("Slow worker started")
        while not self._shutdown_event.is_set():
            try:
                result = self.redis.blpop(self.slow_queue_key, timeout=5)
                if not result:
                    continue
                _, task_data = result
                task = self._deserialize(task_data)
                if task is None:
                    self.logger.error("Slow worker: failed to deserialize task")
                    continue
                self._process_single_task(task, self.slow_processing_key)
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
                threshold = self.app.config.get("RAG_RELEVANCE_THRESHOLD_REASONING", 0.7)
            else:
                threshold = self.app.config.get("RAG_RELEVANCE_THRESHOLD_DEFAULT", 0.5)
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
        return None, None

    def _build_error_response(self, session_id: str, error: str, process_time: float, lang: str) -> dict[str, Any]:
        """Build a standardized error response dict and save to DB."""
        from .db import save_message

        completion_time = get_current_time_in_timezone_for_db(self.app)
        msg_id = save_message(
            session_id, "assistant", "⚠️ " + error, model_name="system", response_time=str(process_time)
        )
        return {
            "error": error,
            "session_id": session_id,
            "assistant_timestamp": completion_time,
            "is_error": True,
            "response_time": process_time,
            "message_id": msg_id,
        }

    def _build_success_response(
        self,
        session_id: str,
        response: str,
        model_used: str,
        process_time: float,
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

    def _save_and_respond(
        self,
        session_id: str,
        text: str,
        model_name: str,
        process_time: float,
        is_error: bool = False,
        file_data=None,
        file_type=None,
        file_name=None,
        file_path=None,
        extra: dict | None = None,
        response_style: str = "neutral",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Save assistant message to DB and return response dict."""
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
        result["response_style"] = response_style
        result["completion_tokens"] = completion_tokens
        return result

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
    ) -> dict[str, Any]:
        """Handle image editing request (image uploaded + edit comment)."""
        mm_start = time.time()
        edit_data, error = self.app.modules["multimodal"].generate_edit_params(message_text, file_data, lang=lang)
        mm_time = round(time.time() - mm_start, 1)
        if error:
            return self._build_error_response(session_id, error, mm_time, lang)

        edit_start = time.time()
        image_result = self.app.modules["image"].edit_image(edit_data, file_data, lang=lang)
        edit_time = round(time.time() - edit_start, 1)

        if not image_result["success"]:
            return self._build_error_response(
                session_id, image_result.get("error", "Image editing failed"), mm_time + edit_time, lang
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
            extra=extra,
            response_style=response_style,
        )

    def _process_image_gen_task(
        self, query: str, session_id: str, user_id: str, lang: str, response_style: str = "neutral"
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

        mm_start = time.time()
        prompt_data, error = self.app.modules["multimodal"].generate_image_params(
            query, lang=lang, response_style=response_style
        )
        mm_time = round(time.time() - mm_start, 1)
        if error:
            return self._build_error_response(session_id, error, mm_time, lang)

        gen_start = time.time()
        image_result = self.app.modules["image"]._call_wrapper(prompt_data, lang=lang)
        gen_time = round(time.time() - gen_start, 1)

        if not image_result["success"]:
            return self._build_error_response(session_id, image_result["error"], mm_time + gen_time, lang)

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
            extra=extra,
            response_style=response_style,
        )

    def _process_video_gen_task(
        self, query: str, session_id: str, user_id: str, lang: str, response_style: str = "neutral"
    ) -> dict[str, Any]:
        """Handle video generation from text (router action_type='video')."""
        if "video" not in self.app.modules:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Video generation module unavailable", lang=lang), 0, lang
            )
        self.app.modules["video"].check_availability()
        if not self.app.modules["video"].available:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Video generation module unavailable", lang=lang), 0, lang
            )

        mm_start = time.time()
        prompt_data, error = self.app.modules["multimodal"].generate_video_params(
            query, lang=lang, response_style=response_style
        )
        mm_time = round(time.time() - mm_start, 1)
        if error:
            return self._build_error_response(session_id, error, mm_time, lang)

        gen_start = time.time()
        video_result = self.app.modules["video"].generate_video(prompt_data, lang=lang)
        gen_time = round(time.time() - gen_start, 1)

        if not video_result["success"]:
            return self._build_error_response(session_id, video_result["error"], mm_time + gen_time, lang)

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
            "response_time": {"mm_time": mm_time, "gen_time": gen_time, "mm_model": mm_model, "gen_model": video_model},
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
            extra=extra,
            response_style=response_style,
        )

    def _process_video_gen_task_from_image(
        self, query: str, image_data: str, session_id: str, user_id: str, lang: str, response_style: str = "neutral"
    ) -> dict[str, Any]:
        """Handle video generation from image + text ([-VIDEO-] marker route).
        Uses the query text as the video prompt (already classified by multimodal)
        with default video parameters — avoids a second multimodal call.
        """
        if "video" not in self.app.modules:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Video generation module unavailable", lang=lang), 0, lang
            )
        self.app.modules["video"].check_availability()
        if not self.app.modules["video"].available:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("Video generation module unavailable", lang=lang), 0, lang
            )

        mm_start = time.time()
        mm_model = self.app.modules.get("multimodal")
        prompt_data, error = (
            mm_model.generate_video_params_from_image(query, image_data, lang=lang, response_style=response_style)
            if mm_model
            else (None, self.app.modules["base"]._("Multimodal model unavailable", lang=lang))
        )
        mm_time = round(time.time() - mm_start, 1)
        if error:
            return self._build_error_response(session_id, error, mm_time, lang)

        gen_start = time.time()
        video_result = self.app.modules["video"].generate_video(prompt_data, image_data=image_data, lang=lang)
        gen_time = round(time.time() - gen_start, 1)

        if not video_result["success"]:
            return self._build_error_response(session_id, video_result["error"], mm_time + gen_time, lang)

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
                    .format(max_w=896, max_h=896, orig_w=orig_w, orig_h=orig_h, new_w=new_w, new_h=new_h)
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
            extra=extra,
            response_style=response_style,
        )

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
            },
            response_style=response_style,
        )

        messages = [first_message]
        if message_text and "multimodal" in self.app.modules and self.app.modules["multimodal"].available:
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
                session_id, bot_reply, mm_model, mm_time, is_error=bool(error), response_style=response_style
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
        stream_start = time.time()
        full_response = ""
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
            self._publish_stream_token(task, token)
            if self._is_task_cancelled(task["id"]):
                cancelled = True
                break
        mm_time = round(time.time() - stream_start, 1)
        model_used = self._get_model_name("multimodal") or "unknown"
        if cancelled:
            self.app.logger.info(f"Task {task['id']} cancelled during camera stream")
            self._publish_stream_event(task, "stream_cancelled")
        return self._save_and_respond(
            session_id,
            full_response,
            model_used,
            mm_time,
            response_style=response_style,
        )

    def _process_rag_task(
        self, query: str, session_id: str, user_id: str, lang: str, response_style: str = "neutral"
    ) -> dict[str, Any]:
        """Handle explicit RAG request (router action_type='rag')."""
        rag_start = time.time()
        rag_answer, rag_model = self._try_rag_answer(
            query, session_id, user_id, lang, strict=False, response_style=response_style
        )
        rag_time = round(time.time() - rag_start, 1)

        if rag_answer is not None:
            model_used = (rag_model + " (RAG)") if rag_model else "unknown (RAG)"
            return self._save_and_respond(session_id, rag_answer, model_used, rag_time, response_style=response_style)
        else:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("No relevant documents found", lang), rag_time, lang
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
        """Handle explicit RAG request with streaming for the LLM generation part."""
        rag_start = time.time()

        def publish_token(token: str) -> None:
            self._publish_stream_token(task, token)

        rag_answer, rag_model = self._try_rag_answer(
            query,
            session_id,
            user_id,
            lang,
            strict=False,
            response_style=response_style,
            token_callback=publish_token,
        )
        rag_time = round(time.time() - rag_start, 1)

        if rag_answer is not None:
            model_used = (rag_model + " (RAG)") if rag_model else "unknown (RAG)"
            return self._save_and_respond(session_id, rag_answer, model_used, rag_time, response_style=response_style)
        else:
            return self._build_error_response(
                session_id, self.app.modules["base"]._("No relevant documents found", lang), rag_time, lang
            )

    def _process_text_task(
        self,
        message_text: str,
        session_id: str,
        user_id: str,
        current_time_str: str,
        lang: str,
        response_style: str = "neutral",
    ) -> dict[str, Any]:
        """Handle text request — routes through base module router."""
        router_start = time.time()
        router_result = self.app.modules["base"].process_message(
            message_text, current_time_str, lang=lang, session_id=session_id, response_style=response_style
        )
        router_time = round(time.time() - router_start, 1)

        if "error" in router_result:
            return self._build_error_response(session_id, router_result["error"], router_time, lang)

        action_type = router_result["action"]
        query = router_result["query"]

        if action_type == "reasoning":
            rag_start = time.time()
            rag_answer, rag_model = self._try_rag_answer(
                query, session_id, user_id, lang, strict=True, response_style=response_style
            )
            rag_time = round(time.time() - rag_start, 1)
            if rag_answer is not None:
                model_used = rag_model + " (RAG)" if rag_model else "unknown (RAG)"
                return self._save_and_respond(
                    session_id, rag_answer, model_used, rag_time, response_style=response_style, user_id=user_id
                )
            self.app.logger.info(f"RAG returned no answer, falling back to reasoning model for query: {query[:50]}...")
            action_type = "reasoning"
            process_time = router_time

        if action_type == "image":
            return self._process_image_gen_task(query, session_id, user_id, lang, response_style)
        elif action_type == "video":
            return self._process_video_gen_task(query, session_id, user_id, lang, response_style)
        elif action_type == "camera":
            return self._process_camera_task(
                query, session_id, user_id, message_text, current_time_str, lang, response_style
            )
        elif action_type == "rag":
            return self._process_rag_task(query, session_id, user_id, lang, response_style)
        elif action_type == "reasoning":
            if router_result.get("needs_reasoning"):
                reasoning_start = time.time()
                final_response = self.app.modules["base"].process_reasoning(
                    query, current_time_str, lang=lang, session_id=session_id, response_style=response_style
                )
                process_time = round(time.time() - reasoning_start, 1)
            else:
                process_time = 0
                final_response = query
            model_used = self._get_model_name("reasoning") or "unknown"
            return self._save_and_respond(
                session_id, final_response, model_used, process_time, response_style=response_style, user_id=user_id
            )
        else:
            return self._save_and_respond(
                session_id,
                query,
                self._get_model_name("chat") or "unknown",
                router_time,
                response_style=response_style,
                user_id=user_id,
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
            message_text, current_time_str, lang=lang, session_id=session_id, response_style=response_style
        )
        router_time = round(time.time() - router_start, 1)

        if "error" in router_result:
            return self._build_error_response(session_id, router_result["error"], router_time, lang)

        action_type = router_result["action"]
        query = router_result["query"]

        # Stream reasoning model responses token by token
        if action_type == "reasoning" and router_result.get("needs_reasoning"):
            # Try RAG first (same as non-streaming path)
            def rag_publish(token: str) -> None:
                self._publish_stream_token(task, token)

            rag_answer, rag_model = self._try_rag_answer(
                query,
                session_id,
                user_id,
                lang,
                strict=True,
                response_style=response_style,
                token_callback=rag_publish,
            )
            if rag_answer is not None:
                model_used = rag_model + " (RAG)" if rag_model else "unknown (RAG)"
                return self._save_and_respond(
                    session_id,
                    rag_answer,
                    model_used,
                    round(time.time() - router_start, 1),
                    response_style=response_style,
                    user_id=user_id,
                )

            stream_start = time.time()
            full_response = ""
            cancelled = False
            for token in self.app.modules["base"].generate_reasoning_response_stream(
                query, current_time_str, lang=lang, session_id=session_id, response_style=response_style
            ):
                full_response += token
                self._publish_stream_token(task, token)
                if self._is_task_cancelled(task["id"]):
                    cancelled = True
                    break

            process_time = round(time.time() - stream_start, 1)
            model_used = self._get_model_name("reasoning") or "unknown"

            # Safety: strip reasoning marker if model leaked it
            marker = "[-REASONING-]"
            if marker in full_response:
                self.app.logger.warning(f"Reasoning model returned marker for task {task['id']}: {full_response[:100]}")
                full_response = full_response.replace(marker, "").strip()

            # Safety: handle empty response
            if not full_response.strip():
                full_response = "⚠️ " + self.app.modules["base"]._("Reasoning model returned empty response", lang)

            if cancelled:
                self.app.logger.info(f"Task {task['id']} cancelled during reasoning stream")
                self._publish_stream_event(task, "stream_cancelled")
            return self._save_and_respond(
                session_id,
                full_response,
                model_used,
                process_time,
                response_style=response_style,
                user_id=user_id,
            )

        # Direct-response actions: router already generated the answer in `query`
        if action_type == "none" or (action_type == "reasoning" and not router_result.get("needs_reasoning")):
            return self._save_and_respond(
                session_id,
                query,
                self._get_model_name("chat") or "unknown",
                router_time,
                response_style=response_style,
                user_id=user_id,
            )

        # Stream-aware actions — dispatch directly, no second router call
        if action_type == "rag":
            return self._process_rag_task_stream(task, query, session_id, user_id, lang, response_style)

        if action_type == "image":
            return self._process_image_gen_task(query, session_id, user_id, lang, response_style)

        if action_type == "video":
            return self._process_video_gen_task(query, session_id, user_id, lang, response_style)

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
            response_style=response_style,
            user_id=user_id,
        )

    # Modified: removed hardcoded is_image_edit block; all image+text now go through _process_image_chat_task
    def _process_request(self, task: dict[str, Any]) -> dict[str, Any]:
        """Main entry point — delegates to specialized task handlers."""
        self.app.logger.info(f"RedisRequestQueue._process_request: processing task {task['id']}")

        task_type = task.get("type") or task.get("data", {}).get("type")
        self.app.logger.info(f"_process_request: task_type={task_type}, task_id={task.get('id')}")

        if task_type == "index_document":
            self.app.logger.info(f"_process_request: calling _process_index_task for task {task.get('id')}")
            return self._process_index_task(task)
        if task_type == "reindex_all_embeddings":
            return self._process_reindex_all_task(task)
        if task_type == "transcribe_audio":
            return self._process_transcribe_task(task)

        user_id = task["user_id"]
        session_id = task["session_id"]
        request_data = task["data"]
        lang = task.get("lang", "ru")
        current_time_str = get_current_time_in_timezone(self.app)
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
                )  # type: ignore[arg-type]
            return self._process_text_task(message_text, session_id, user_id, current_time_str, lang, response_style)  # type: ignore[arg-type]

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
                    user_id,  # type: ignore[arg-type]
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
                user_id,  # type: ignore[arg-type]
                response_style,
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
                            # Redirect to video generation task (image+text)
                            return self._process_video_gen_task_from_image(
                                video_query, file_data, session_id, user_id, lang, response_style
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

        mm_model = self._get_model_name("multimodal") or "unknown"
        return self._save_and_respond(
            session_id, bot_reply, mm_model, process_time, is_error=is_error, response_style=response_style
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
                "unknown",
                process_time,
                is_error=True,
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
                "unknown",
                process_time,
                is_error=True,
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
            for token in stream_gen:
                full_response += token
                self._publish_stream_token(task, token)
                if self._is_task_cancelled(task["id"]):
                    cancelled = True
                    break
            process_time = round(time.time() - process_start, 1)
            mm_model = self._get_model_name("multimodal") or "unknown"
            if cancelled:
                self.app.logger.info(f"Task {task['id']} cancelled during image chat stream")
                self._publish_stream_event(task, "stream_cancelled")
            return self._save_and_respond(
                session_id,
                full_response,
                mm_model,
                process_time,
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
                        )
                    bot_reply = "⚠️ " + self.app.modules["base"]._("Image editing request was empty", lang)
                    process_time = round(time.time() - process_start, 1)
                    return self._save_and_respond(
                        session_id,
                        bot_reply,
                        "unknown",
                        process_time,
                        is_error=True,
                        response_style=response_style,
                    )
                if video_marker in buffer:
                    for token in stream_gen:
                        full_response += token
                    video_query = full_response.split(video_marker, 1)[1].strip()
                    if video_query:
                        return self._process_video_gen_task_from_image(
                            video_query, file_data, session_id, user_id, lang, response_style
                        )
                    bot_reply = "⚠️ " + self.app.modules["base"]._("Video request was empty", lang)
                    process_time = round(time.time() - process_start, 1)
                    return self._save_and_respond(
                        session_id,
                        bot_reply,
                        "unknown",
                        process_time,
                        is_error=True,
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
        return self._save_and_respond(
            session_id,
            full_response,
            mm_model,
            process_time,
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
            text_request_data = {
                "type": "text",
                "text": transcribed_text,
                "preview": (transcribed_text[:50] + "...")
                if transcribed_text
                else self.app.modules["base"]._("Voice request", lang=lang),
                "response_style": response_style,
                "stream": True,
            }
            new_request_id, _ = self.app.request_queue.add_request(
                user_id, session_id, text_request_data, user_class, lang=lang
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
            error_msg = "RAG module unavailable"
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
            return {"success": False, "error": "RAG module unavailable"}

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

    def get_user_requests_status(self, user_id: str, lang: str = "ru") -> dict[str, Any]:
        """Get status of user's requests (processing, queued, completed)."""
        result: dict = {"processing": None, "queued": [], "recent_completed": []}

        processing_session_ids = set()

        for proc_key in [self.processing_key, self.slow_processing_key]:
            processing_tasks = self.redis.hgetall(proc_key)
            for req_id, task_data in processing_tasks.items():
                req_id = req_id.decode() if isinstance(req_id, bytes) else req_id
                task = self._deserialize(task_data)
                if task and task.get("user_id") == user_id:
                    task["status"] = "processing"
                    task["position_info"] = {"position": 1, "estimated_seconds": 0}
                    result["processing"] = self._format_request_info(task, lang)
                    if task.get("session_id"):
                        processing_session_ids.add(task.get("session_id"))
                    break
            if result["processing"]:
                break

        position = 1
        for q_key in [self.queue_key, self.slow_queue_key]:
            queue_length = self.redis.llen(q_key)
            queue_tasks = self.redis.lrange(q_key, 0, queue_length - 1) if queue_length > 0 else []
            for task_data in queue_tasks:
                task = self._deserialize(task_data)
                if task and task.get("user_id") == user_id:
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
            "position_info": task.get("position_info", {"position": "?", "estimated_seconds": 5}),
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

    def cancel_task(self, task_id: str) -> bool:
        """Mark a task as cancelled in Redis. Returns True if the task exists."""
        exists = self.redis.hexists(self.processing_key, task_id)
        if not exists:
            exists = self.redis.hexists(self.results_key, task_id)
        if not exists:
            return False
        ttl = self.app.config.get("QUEUE_MAX_WAIT_TIME", 300) + 120
        self.redis.setex(f"task:cancel:{task_id}", ttl, "1")
        self.app.logger.info(f"Task {task_id} marked as cancelled")
        return True

    def _is_task_cancelled(self, task_id: str) -> bool:
        """Check if a task has been cancelled (polled by the streaming worker)."""
        return bool(self.redis.exists(f"task:cancel:{task_id}"))
