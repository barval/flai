"""Bridge between the public API blueprint and the existing chat queue."""

import base64
import contextlib
import hashlib
import json
import struct
import time
from typing import Any, cast

import redis as redis_lib
from flask import current_app

from app.db import create_session, save_message, update_session_visit
from app.utils import validate_session_ownership

CONVERSATION_KEY_PREFIX = "api_conv:"
SESSION_TITLE_PREFIX = "flai:conv:"
DEFAULT_SESSION_TITLE = "flai:api"
RESULT_POLL_INTERVAL_S = 2.0
API_CHAT_MODEL = "flai-chat"
API_EMBEDDINGS_MODEL = "flai-embeddings"
DEFAULT_RESPONSE_STYLE = "neutral"
DEFAULT_SERVICE_CLASS = 2
PREVIEW_CHARS = 50


class ApiSessionNotFoundError(Exception):
    """Raised when a requested session does not belong to the API caller."""


class ApiLocalizedError(Exception):
    """Base for API errors that carry an untranslated message id.

    The route translates ``msgid`` so the caller sees the error in their own
    language, keeping every user-facing string in the catalogs.
    """

    def __init__(self, msgid: str, *args: Any) -> None:
        super().__init__(msgid)
        self.msgid = msgid
        self.msgid_args = args

    def localized(self, translate: Any) -> str:
        """Return the translated message, applying ``%``-style arguments."""
        text: str = translate(self.msgid)
        return text % self.msgid_args if self.msgid_args else text


class ApiTaskError(ApiLocalizedError):
    """Raised when the queue reports a terminal error for an API task."""


class ApiTaskTimeoutError(Exception):
    """Raised when an API task is still running after the sync wait budget."""

    def __init__(self, task_id: str) -> None:
        super().__init__(task_id)
        self.task_id = task_id


class ApiImageRejectedError(ApiLocalizedError):
    """Raised when an OpenAI image content part cannot be used."""


def get_request_queue() -> Any:
    """Return the shared RedisRequestQueue of the running app."""
    return cast(Any, current_app).request_queue


def get_redis_client() -> redis_lib.Redis:
    """Return a new Redis client. Every caller closes the client it receives."""
    return redis_lib.from_url(current_app.config["REDIS_URL"], decode_responses=True)


API_TASK_KEY_PREFIX = "api:task:"
API_TASKS_KEY_PREFIX = "api:tasks:"
API_TASK_INDEX_MAX = 500
REGISTER_API_TASK_SCRIPT = """
local values = redis.call('HGETALL', KEYS[1])
local existing = {}
for i = 1, #values, 2 do existing[values[i]] = values[i + 1] end
local login = ARGV[1]
local session_id = ARGV[2]
local endpoint = ARGV[3]
local created_at = ARGV[4]
local ttl = tonumber(ARGV[5])
local task_id = ARGV[6]
local max_tasks = tonumber(ARGV[7])
local only_if_absent = ARGV[8] == '1'
if only_if_absent and next(existing) then
    if not existing.login or not existing.session_id or not existing.endpoint or not existing.created_at then
        return 0
    end
    if existing.login ~= login then return 0 end
    if existing.session_id ~= session_id or existing.endpoint ~= endpoint then return 0 end
    created_at = existing.created_at
else
    redis.call('HSET', KEYS[1], 'login', login, 'session_id', session_id, 'endpoint', endpoint, 'created_at', created_at)
end
redis.call('ZADD', KEYS[2], created_at, task_id)
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('EXPIRE', KEYS[2], ttl)
local count = redis.call('ZCARD', KEYS[2])
if count > max_tasks then redis.call('ZREMRANGEBYRANK', KEYS[2], 0, count - max_tasks - 1) end
return 1
"""
API_TASK_RESULT_FIELDS = frozenset(
    {
        "response",
        "text",
        "model",
        "model_used",
        "prompt_tokens",
        "completion_tokens",
        "is_error",
        "usage",
        "status",
    }
)


def register_api_task(
    api_user: dict[str, Any],
    task_id: str,
    session_id: str,
    endpoint: str,
    only_if_absent: bool = False,
) -> bool:
    """Index a queue task under its API owner for status/cancel authorization.

    For requeued IDs, ``only_if_absent`` reserves the login field with HSETNX
    and refuses to overwrite a task already owned by a different API user.
    """
    login = str(api_user["login"])
    created_at = time.time()
    task_key = f"{API_TASK_KEY_PREFIX}{task_id}"
    index_key = f"{API_TASKS_KEY_PREFIX}{login}"
    session_value = session_id or ""
    client = get_redis_client()
    try:
        ttl = int(current_app.config.get("REDIS_RESULT_TTL", 3600))
        result = client.eval(
            REGISTER_API_TASK_SCRIPT,
            2,
            task_key,
            index_key,
            login,
            session_value,
            endpoint,
            str(created_at),
            ttl,
            task_id,
            API_TASK_INDEX_MAX,
            "1" if only_if_absent else "0",
        )
        return bool(result)
    finally:
        client.close()


def get_api_task_owner(task_id: str) -> dict[str, str] | None:
    """Return registry metadata, or ``None`` when the task is unknown/expired."""
    client = get_redis_client()
    try:
        metadata = client.hgetall(f"{API_TASK_KEY_PREFIX}{task_id}")
    finally:
        client.close()
    if not metadata:
        return None
    return {str(key): str(value) for key, value in metadata.items()}


def list_api_tasks(login: str, limit: int = 20) -> list[dict[str, str]]:
    """List the most recently registered tasks belonging to one API owner."""
    client = get_redis_client()
    try:
        task_ids = client.zrevrange(f"{API_TASKS_KEY_PREFIX}{login}", 0, max(0, limit - 1))
        tasks: list[dict[str, str]] = []
        for task_id in task_ids:
            if isinstance(task_id, bytes):
                task_id = task_id.decode("utf-8")
            metadata = client.hgetall(f"{API_TASK_KEY_PREFIX}{task_id}")
            if metadata and metadata.get("login") == login:
                tasks.append({"task_id": str(task_id), **{str(k): str(v) for k, v in metadata.items()}})
        return tasks
    finally:
        client.close()


def sanitize_api_task_result(result: dict[str, Any]) -> dict[str, Any]:
    """Expose safe task result fields, never paths, file bytes or credentials."""
    safe = {key: value for key, value in result.items() if key in API_TASK_RESULT_FIELDS and key != "status"}
    usage = safe.get("usage")
    if isinstance(usage, dict):
        safe["usage"] = {key: value for key, value in usage.items() if key in ("prompt_tokens", "completion_tokens")}
    error = result.get("error")
    if isinstance(error, str) and error.startswith("⚠️ "):
        safe["error"] = error
    return safe


def client_fingerprint(client_user: str) -> str:
    """Return a stable, non-reversible fingerprint of a client-supplied user id."""
    return hashlib.sha1(client_user.encode("utf-8")).hexdigest()  # noqa: S324 - key hygiene, not security


def conversation_key(login: str, client_user: str) -> str:
    """Return the Redis key mapping an API caller to a persistent session."""
    return f"{CONVERSATION_KEY_PREFIX}{login}:{client_fingerprint(client_user)}"


def create_api_session(api_user: dict[str, Any], client_user: str | None) -> str:
    """Persist a new chat session for the API caller and return its id."""
    title = f"{SESSION_TITLE_PREFIX}{client_fingerprint(client_user)[:32]}" if client_user else DEFAULT_SESSION_TITLE
    session_id: str = create_session(user_id=api_user["login"], title=title, lang=api_user.get("language", "ru"))
    return session_id


def resolve_api_session(
    api_user: dict[str, Any],
    requested_session_id: str | None = None,
    client_user: str | None = None,
) -> str:
    """Resolve the chat session an API request must use.

    An explicit ``requested_session_id`` is honoured only when the API caller
    owns it. Otherwise a ``client_user`` value maps to a persistent session
    through Redis, and a caller without either gets a fresh session. The
    cookie-backed Flask session is never read or written.
    """
    login = api_user["login"]

    if requested_session_id:
        if not validate_session_ownership(requested_session_id, login):
            raise ApiSessionNotFoundError(requested_session_id)
        return requested_session_id

    if not client_user:
        return create_api_session(api_user, None)

    key = conversation_key(login, client_user)
    client = get_redis_client()
    try:
        cached: str | None = client.get(key)
        if cached and validate_session_ownership(cached, login):
            return cached
        session_id = create_api_session(api_user, client_user)
        client.set(key, session_id)
        return session_id
    finally:
        client.close()


def parse_data_url(url: str) -> tuple[str, str, str]:
    """Split an OpenAI ``data:`` image URL into (file_data, file_type, file_name)."""
    from app.utils import resize_image_if_needed

    if not isinstance(url, str) or not url.startswith("data:"):
        raise ApiImageRejectedError("Only data: URLs are supported for image content parts")

    header, separator, payload = url.partition(",")
    if not separator or "base64" not in header:
        raise ApiImageRejectedError("Image data URL must be base64 encoded")

    file_type = header[len("data:") :].split(";", 1)[0].strip()
    if not file_type.startswith("image/"):
        raise ApiImageRejectedError("Only image content parts are supported")

    file_data = payload.strip()
    if not file_data:
        raise ApiImageRejectedError("Image data URL is empty")

    file_name = f"image.{file_type.split('/', 1)[1]}" if "/" in file_type else "image.png"
    max_size = current_app.config.get("MAX_IMAGE_SIZE", 1536)
    new_data, new_type, new_name, _resized, _orig, _new = resize_image_if_needed(
        file_data, file_type, file_name, max_size
    )
    return new_data, new_type, new_name


def normalize_chat_messages(messages: Any) -> tuple[str, list[dict[str, str]]]:
    """Return the concatenated user text and the first image of an OpenAI request.

    A FLAI task carries at most one image, so the first image part wins and any
    later one is dropped rather than silently mis-attached.
    """
    if not isinstance(messages, list) or not messages:
        raise ApiImageRejectedError("'messages' must be a non-empty array")

    texts: list[str] = []
    images: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ApiImageRejectedError("Each entry of 'messages' must be an object")

        role = message.get("role")
        if role not in ("user", "assistant", "system", "developer"):
            raise ApiImageRejectedError("Each message needs a supported 'role'")

        content = message.get("content")
        if content is None:
            continue
        if isinstance(content, str):
            if content:
                texts.append(content)
            continue
        if not isinstance(content, list):
            raise ApiImageRejectedError("Message content must be a string or an array of parts")

        for part in content:
            if not isinstance(part, dict):
                raise ApiImageRejectedError("Each content part must be an object")
            part_type = part.get("type")
            if part_type == "text":
                part_text = part.get("text")
                if isinstance(part_text, str) and part_text:
                    texts.append(part_text)
            elif part_type == "image_url":
                image_url = part.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url
                if not isinstance(url, str):
                    raise ApiImageRejectedError("Only data: URLs are supported for image content parts")
                file_data, file_type, file_name = parse_data_url(url)
                if not images:
                    images.append({"file_data": file_data, "file_type": file_type, "file_name": file_name})
            else:
                raise ApiImageRejectedError("Unsupported content part type: %s", part_type)

    text = "\n".join(texts).strip()
    if not text and not images:
        raise ApiImageRejectedError("'messages' contains no text or image content")
    return text, images


def _build_user_content(text: str, images: list[dict[str, str]]) -> str:
    """Serialize a user turn the way the web route stores it."""
    content: list[dict[str, str]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.extend(images)
    return json.dumps(content, ensure_ascii=False)


def enqueue_chat(
    api_user: dict[str, Any],
    session_id: str,
    text: str,
    images: list[dict[str, str]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Persist an API user turn and submit it to the normal chat queue.

    Returns the queue task id and position info. The task is always a web-shaped
    text or image task, so the LLM router — not the API — decides what happens.
    """
    images = images or []
    login = api_user["login"]
    lang = api_user.get("language", "ru")
    response_style = api_user.get("response_style", DEFAULT_RESPONSE_STYLE)
    service_class = api_user.get("service_class", DEFAULT_SERVICE_CLASS)

    file_data = file_type = file_name = None
    if images:
        file_data = images[0]["file_data"]
        file_type = images[0]["file_type"]
        file_name = images[0]["file_name"]

    message_id = save_message(
        session_id,
        "user",
        _build_user_content(text, images),
        file_data,
        file_type,
        file_name,
        user_id=login,
        response_style=response_style,
    )
    update_session_visit(login, session_id)

    preview = text[:PREVIEW_CHARS] if text else (file_name or "")
    if images:
        request_data: dict[str, Any] = {
            "type": "image",
            "text": text,
            "file_data": file_data,
            "file_type": file_type,
            "file_name": file_name,
            "preview": f"{preview}..." if text else (file_name or "Image"),
            "response_style": response_style,
            "stream": True,
        }
    else:
        request_data = {
            "type": "text",
            "text": text,
            "current_message_id": message_id,
            "preview": f"{preview}..." if text else "Text request",
            "response_style": response_style,
            "stream": True,
        }

    task_id, queue_info = get_request_queue().add_request(login, session_id, request_data, service_class, lang)
    return str(task_id), dict(queue_info)


def _next_task_id(event_data: dict[str, Any]) -> str | None:
    """Return the requeued task id when a completed envelope is not final."""
    inner = event_data.get("result")
    if isinstance(inner, dict) and inner.get("status") == "queued":
        return inner.get("request_id")
    return None


def _read_events(pubsub: Any, active_id: str) -> dict[str, Any] | None:
    """Drain the pub/sub stream and return the terminal result for *active_id*."""
    while True:
        message = pubsub.get_message(timeout=RESULT_POLL_INTERVAL_S)
        if not message or message.get("type") != "message":
            return None
        try:
            envelope = json.loads(message["data"])
        except (TypeError, ValueError):
            continue
        if envelope.get("type") != "result_completed":
            continue
        data = envelope.get("data") or {}
        if data.get("task_id") != active_id:
            continue
        return data


def wait_for_result(login: str, task_id: str, timeout_s: float) -> dict[str, Any]:
    """Block until a queued task reaches a terminal state, following requeues.

    The queue requeues a fast CPU phase onto the slow GPU worker, so an outer
    "completed" envelope may only mean "this phase finished". Nested requeues are
    followed until a task returns a real result, an error, or the wait budget runs out.
    """
    queue = get_request_queue()
    client = get_redis_client()
    pubsub = client.pubsub()
    pubsub.subscribe(f"user:events:{login}")

    active_id = task_id
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            stored = queue.check_result(active_id)
            if stored:
                nxt = _next_task_id(stored)
                if nxt:
                    if not _register_requeued_api_task(login, active_id, nxt):
                        raise ApiTaskError("Task %s failed", active_id)
                    active_id = nxt
                    continue
                return _resolve_terminal(active_id, stored)

            event_data = _read_events(pubsub, active_id)
            if event_data is not None:
                nxt = _next_task_id(event_data)
                if nxt:
                    if not _register_requeued_api_task(login, active_id, nxt):
                        raise ApiTaskError("Task %s failed", active_id)
                    active_id = nxt
                    continue
                return _resolve_terminal(active_id, event_data)

            if time.monotonic() >= deadline:
                raise ApiTaskTimeoutError(active_id)
    finally:
        _close_pubsub(pubsub, client)


def _register_requeued_api_task(login: str, previous_id: str, next_id: str) -> bool:
    """Carry API-task ownership to a requeued child task before following it."""
    metadata = get_api_task_owner(previous_id)
    if not metadata:
        return False
    if metadata.get("login") != login:
        return False
    return register_api_task(
        {"login": login},
        next_id,
        metadata.get("session_id", ""),
        metadata.get("endpoint", ""),
        only_if_absent=True,
    )


def _resolve_terminal(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Turn a terminal queue payload into the inner result or raise."""
    if payload.get("status") == "error":
        inner = payload.get("result") or {}
        error = inner.get("error") or payload.get("error")
        if isinstance(error, str) and error.startswith("⚠️ "):
            raise ApiTaskError(error)
        raise ApiTaskError("Task %s failed", task_id)

    inner = payload.get("result")
    if not isinstance(inner, dict):
        raise ApiTaskError("Task %s returned no result", task_id)
    if inner.get("is_error") and "response" not in inner:
        # A handler failed without producing an answer. Chat errors that carry a
        # persisted response stay 200 so the caller sees the same ⚠️ text the web
        # chat shows, matching OpenAI's "the model answered" semantics.
        error = inner.get("error")
        if isinstance(error, str) and error.startswith("⚠️ "):
            raise ApiTaskError(error)
        raise ApiTaskError("Task %s failed", task_id)
    nxt = _next_task_id({"result": inner})
    if nxt:
        raise ApiTaskError("Task %s was requeued to %s without a follow-up", task_id, nxt)
    return inner


def _close_pubsub(pubsub: Any, client: Any) -> None:
    with contextlib.suppress(Exception):
        pubsub.unsubscribe()
    with contextlib.suppress(Exception):
        client.close()


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def serialize_chat_completion(result: dict[str, Any], task_id: str, session_id: str) -> dict[str, Any]:
    """Build an OpenAI ``chat.completion`` body from a queue result."""
    prompt_tokens = _int_or_zero(result.get("prompt_tokens"))
    completion_tokens = _int_or_zero(result.get("completion_tokens"))
    return {
        "id": f"chatcmpl-{task_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": API_CHAT_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.get("response", "")},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "flai_session_id": session_id,
    }


def _now() -> float:
    """Monotonic clock seam, kept separate so streaming can be tested."""
    return time.monotonic()


def _sse(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


SSE_DONE = "data: [DONE]\n\n"
SSE_HEARTBEAT_S = 15.0
SSE_POLL_INTERVAL_S = 0.5


def _translate(translate: Any, msgid: str, *args: Any) -> str:
    text = translate(msgid) if translate else msgid
    return text % args if args else text


def _chunk(
    task_id: str,
    created: int,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Build one ``chat.completion.chunk`` body."""
    body: dict[str, Any] = {
        "id": f"chatcmpl-{task_id}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": API_CHAT_MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        body["choices"] = []
        body["usage"] = usage
    if session_id:
        body["flai_session_id"] = session_id
    return body


def _error_payload(message: str, code: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": "server_error", "param": None, "code": code}}


def _stream_terminal(
    status: str,
    result: dict[str, Any],
    task_id: str,
    created: int,
    emitted: str,
    session_id: str,
    include_usage: bool,
    translate: Any,
) -> Any:
    """Yield the closing chunks of a finished task and terminate the stream."""
    if status == "error":
        message = (result or {}).get("error")
        if not isinstance(message, str) or not message.startswith("⚠️ "):
            message = _translate(translate, "Task %s failed", task_id)
        yield _sse(_error_payload(message, "task_failed"))
        yield SSE_DONE
        return
    if result.get("is_error") and "response" not in result:
        yield _sse(_error_payload(_translate(translate, "Task %s failed", task_id), "task_failed"))
        yield SSE_DONE
        return

    final_text = (result or {}).get("response") or ""
    if final_text.startswith(emitted):
        tail = final_text[len(emitted) :]
    elif emitted:
        # Post-processing rewrote the answer after tokens went out; the client
        # already has a complete, coherent stream, so do not duplicate it.
        tail = ""
    else:
        tail = final_text
    if tail:
        yield _sse(_chunk(task_id, created, {"content": tail}))

    yield _sse(_chunk(task_id, created, {}, finish_reason="stop", session_id=session_id))
    if include_usage:
        prompt_tokens = _int_or_zero((result or {}).get("prompt_tokens"))
        completion_tokens = _int_or_zero((result or {}).get("completion_tokens"))
        yield _sse(
            _chunk(
                task_id,
                created,
                {},
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            )
        )
    yield SSE_DONE


def stream_chat(
    login: str,
    task_id: str,
    session_id: str,
    include_usage: bool = False,
    timeout_s: float | None = None,
    translate: Any = None,
) -> Any:
    """Yield OpenAI SSE chunks for a queued task, following requeued phases.

    Tokens are forwarded as they are produced. Progress events and tokens that
    belong to another task are dropped: an API caller only sees the answer.
    A client disconnect closes the subscription without cancelling the task, so
    the answer still lands in the web chat.
    """
    if timeout_s is None:
        timeout_s = current_app.config.get("API_SYNC_MAX_WAIT", 600)

    queue = get_request_queue()
    client = get_redis_client()
    pubsub = client.pubsub()
    pubsub.subscribe(f"user:events:{login}")

    active_id = task_id
    created = int(time.time())
    emitted: list[str] = []
    deadline = _now() + timeout_s
    last_beat = _now()

    try:
        yield _sse(_chunk(task_id, created, {"role": "assistant"}, session_id=session_id))
        while True:
            message = pubsub.get_message(timeout=SSE_POLL_INTERVAL_S)
            if message and message.get("type") == "message":
                try:
                    envelope = json.loads(message["data"])
                except (TypeError, ValueError):
                    envelope = {}
                kind = envelope.get("type")
                data = envelope.get("data") or {}
                if data.get("task_id") != active_id or not isinstance(data, dict):
                    envelope = {}
                elif kind == "stream_token" and data.get("token"):
                    emitted.append(data["token"])
                    yield _sse(_chunk(active_id, created, {"content": data["token"]}))
                elif kind == "result_completed":
                    nxt = _next_task_id(data)
                    if nxt:
                        if not _register_requeued_api_task(login, active_id, nxt):
                            yield _sse(
                                _error_payload(_translate(translate, "Task %s failed", active_id), "task_failed")
                            )
                            yield SSE_DONE
                            return
                        active_id = nxt
                        continue
                    yield from _stream_terminal(
                        data.get("status", "completed"),
                        data.get("result") or {},
                        active_id,
                        created,
                        "".join(emitted),
                        session_id,
                        include_usage,
                        translate,
                    )
                    return
            else:
                stored = queue.check_result(active_id)
                if stored:
                    nxt = _next_task_id(stored)
                    if nxt:
                        if not _register_requeued_api_task(login, active_id, nxt):
                            yield _sse(
                                _error_payload(_translate(translate, "Task %s failed", active_id), "task_failed")
                            )
                            yield SSE_DONE
                            return
                        active_id = nxt
                        continue
                    yield from _stream_terminal(
                        stored.get("status", "completed"),
                        stored.get("result") or {},
                        active_id,
                        created,
                        "".join(emitted),
                        session_id,
                        include_usage,
                        translate,
                    )
                    return

            now = _now()
            if now - last_beat >= SSE_HEARTBEAT_S:
                last_beat = now
                yield ": ping\n\n"
            if now >= deadline:
                message_text = _translate(translate, "Task %s is still running and was not cancelled", active_id)
                yield _sse(_error_payload(message_text, "task_timeout"))
                yield SSE_DONE
                return
    finally:
        _close_pubsub(pubsub, client)


API_EMBEDDINGS_SESSION = "api"


def enqueue_embeddings(api_user: dict[str, Any], texts: list[str]) -> str:
    """Queue a batch embedding request.

    Embeddings are stateless: no chat session is created, no message is
    persisted and nothing appears in the user's chat. The task still goes
    through the queue so it holds the GPU lock like every other model call.
    """
    request_data = {"type": "api_embedding", "input": texts, "stream": False}
    task_id, _queue_info = get_request_queue().add_request(
        api_user["login"],
        API_EMBEDDINGS_SESSION,
        request_data,
        api_user.get("service_class", DEFAULT_SERVICE_CLASS),
        api_user.get("language", "ru"),
    )
    return str(task_id)


def enqueue_transcription(
    api_user: dict[str, Any],
    file_data: str,
    file_type: str | None,
    file_name: str | None,
    language: str | None = None,
) -> str:
    """Queue an audio transcription request.

    Like embeddings this is stateless: the dedicated ``api_transcribe`` task type
    writes no chat message and opens no usage account, so the transcript reaches
    the caller only through the API response. ``language`` overrides the account
    language for this call only.
    """
    request_data = {
        "type": "api_transcribe",
        "file_data": file_data,
        "file_type": file_type,
        "file_name": file_name,
        "stream": False,
    }
    task_id, _queue_info = get_request_queue().add_request(
        api_user["login"],
        API_EMBEDDINGS_SESSION,
        request_data,
        api_user.get("service_class", DEFAULT_SERVICE_CLASS),
        language or api_user.get("language", "ru"),
    )
    return str(task_id)


def _encode_vector(vector: list[float]) -> str:
    """Encode a vector as base64 little-endian float32, the OpenAI wire format."""
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")


def serialize_embeddings(result: dict[str, Any], encoding_format: str = "float") -> dict[str, Any]:
    """Build an OpenAI ``list`` body from a completed embedding task."""
    vectors = result.get("embeddings")
    if not isinstance(vectors, list) or not vectors:
        raise ApiTaskError("Task %s returned no vectors", result.get("task_id", ""))

    encode = _encode_vector if encoding_format == "base64" else list
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": encode(vector)} for index, vector in enumerate(vectors)
        ],
        "model": API_EMBEDDINGS_MODEL,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
