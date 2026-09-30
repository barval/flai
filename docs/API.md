# FLAI OpenAI-Compatible API

FLAI exposes a small, honest subset of the OpenAI HTTP API. Anything a client
needs to integrate with a home automation system, a bot or a script works
through these endpoints.

**The central design decision:** FLAI is a *capability* router, not a model
server. A request is not sent to the weights named in `model`. The prompt goes
to the FLAI router exactly as it would from the web chat, and the router decides
whether the answer needs the reasoning model, a document search, a web search,
the vision model or an image generator. This is why the API can offer
documents, images and web search through a single OpenAI-shaped endpoint, and
why the `model` field is accepted and ignored.

Everything runs on the existing GPU queue, so the API obeys the same
serialization and VRAM rules as the web UI: one model at a time, on one GPU.

---

## Status

| Capability | Endpoint | State |
|---|---|---|
| API keys | Web UI, `/api/api-keys/*` | Shipped |
| Chat completion (sync) | `POST /v1/chat/completions` | Shipped |
| Chat completion (SSE stream) | `POST /v1/chat/completions` with `stream: true` | Shipped |
| Embeddings | `POST /v1/embeddings` | Shipped |
| Model list | `GET /v1/models` | Shipped |
| Identity and capabilities | `GET /v1/flai/me` | Shipped |
| Image and video generation | `/v1/images/*`, `/v1/videos/*` | Planned |
| Audio, documents, deep analysis | `/v1/audio/*`, `/v1/rlm/*` | Planned |

Endpoints that are not listed in this table do not exist yet. A client that
needs them should watch `CHANGELOG.md`.

---

## Authentication

Authentication is a per-user API key sent as a Bearer token. Keys are created
in the web UI (**API keys** in the account menu): the secret is shown exactly
once and only its SHA-256 digest is stored.

```bash
curl http://localhost:5000/v1/models \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"
```

Rules that matter for integrations:

- The key identifies a **user**. Every request runs with that user's models,
  documents, response style and language.
- The API **never** sets a cookie and never touches the Flask web session. A
  browser that holds a valid web session stays logged in and separate; a script
  without a session works fine.
- A revoked key stops working on the next request (`401 invalid_api_key`).
- `last_used_at` is refreshed on every authenticated request.

The whole `/v1` blueprint is exempt from CSRF protection, because a Bearer
token is not a cookie. All other routes keep CSRF.

---

## Session continuity

OpenAI clients are stateless: they resend the full message list on every call.
FLAI can work either way, and the choice is up to the client.

**Stateless (default).** Send the full history in `messages`. FLAI stores the
conversation in its own session and the next turn continues from the stored
history, so the client can behave like a normal OpenAI client.

**Stateful.** Pass a stable `user` value:

```json
{"user": "home-assistant", "messages": [{"role": "user", "content": "What is in the kitchen?"}]}
```

FLAI maps that `(login, user)` pair to one internal chat session and reuses it.
The same `user` on the next call continues the thread; a different value
starts a new one. Reuse a `user` value only from a single integration — two
clients sharing it share a conversation.

**Pinning a session.** Every response carries `flai_session_id`. Send it back
as `metadata.session_id` to continue exactly that session:

```json
{"metadata": {"session_id": "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"}, "messages": [{"role": "user", "content": "And the garden?"}]}
```

A session id that does not belong to the key owner is rejected with
`404 session_not_found`. This is what makes it safe to run one conversation
per room or per device.

The conversation is also visible in the web chat: an API turn is saved as a
normal message pair with the API user's login, so a request made by a script
shows up in that user's history and triggers the same unread badge as a web
message.

---

## `POST /v1/chat/completions`

### Synchronous request

```bash
curl http://localhost:5000/v1/chat/completions \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "flai-chat",
        "user": "home-assistant",
        "messages": [{"role": "user", "content": "Summarize the news about local elections"}]
      }'
```

```json
{
  "id": "chatcmpl-8f1c...",
  "object": "chat.completion",
  "created": 1759000000,
  "model": "flai-chat",
  "choices": [
    {"index": 0, "message": {"role": "assistant", "content": "..."}, "finish_reason": "stop"}
  ],
  "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
  "flai_session_id": "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
}
```

`usage` reports the real token counts recorded by the model that generated the
answer. The value is `0` when a route does not use a tokenized model.

### Streaming request

```bash
curl -N http://localhost:5000/v1/chat/completions \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
        "stream": true,
        "stream_options": {"include_usage": true},
        "messages": [{"role": "user", "content": "Write a haiku about VRAM"}]
      }'
```

The response is `text/event-stream`. Chunks are standard
`chat.completion.chunk` objects; the first one opens the assistant role, the
last one carries `finish_reason: "stop"`, and the stream always ends with
`data: [DONE]`.

```text
data: {"id":"chatcmpl-8f1c...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-8f1c...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"VRAM"},"finish_reason":null}]}

data: {"id":"chatcmpl-8f1c...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"flai_session_id":"3f2b..."}

data: {"id":"chatcmpl-8f1c...","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":31,"completion_tokens":24,"total_tokens":55}}

data: [DONE]
```

Streaming details that are worth knowing:

- Only the answer is streamed. Progress events (`reasoning_thinking`,
  `routing`, `loading_model`) and tokens that belong to another task are
  dropped, so an API client never sees a stage name as assistant text.
- A task that requeues itself onto the slow GPU worker is followed silently.
  The client keeps receiving one continuous stream and gets a single
  `finish_reason`, from the task that produced the final answer.
- A keep-alive comment (`: ping`) is sent every 15 seconds of silence so proxies
  do not cut an idle connection while a model loads.
- If the client disconnects, the task keeps running and the answer still lands
  in the web chat. Only the stream is abandoned.
- `stream_options.include_usage` defaults to `false`. Set it to `true` to get
  the final usage chunk.

### Images

An image is sent as an OpenAI `image_url` content part with an inline `data:`
URL:

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "What is on this photo?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSk..."}}
      ]
    }
  ]
}
```

Constraints: the URL must start with `data:`, be base64 encoded and have an
`image/*` media type. Remote URLs are rejected with `400` — FLAI runs offline
and will not fetch an image from the internet on your behalf. If several image
parts are present, the first one is used and the rest are dropped.

### Accepted and ignored parameters

These are accepted so that stock OpenAI clients work, and they change nothing:

| Parameter | Behavior |
|---|---|
| `model` | Ignored. Any value is accepted, including an unknown one. FLAI routes by capability. The response always reports `flai-chat`. |
| `stream_options` | `include_usage` is honoured; other keys are ignored. |
| `tools`, `tool_choice`, `functions` | Ignored. FLAI calls no external functions. |
| `response_format` | Ignored. Asking for JSON does not force JSON; put "answer with JSON only" in the message instead. |
| `n`, `temperature`, `max_tokens`, `stop`, `presence_penalty` | Accepted and ignored. Generation parameters are fixed per route. |
| `user` | Used for session continuity (see above), not by OpenAI semantics. |

---

## `POST /v1/embeddings`

```bash
curl http://localhost:5000/v1/embeddings \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"input": ["first document", "second document"], "encoding_format": "float"}'
```

```json
{
  "object": "list",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.0123, -0.0456]},
    {"object": "embedding", "index": 1, "embedding": [0.0789, 0.0011]}
  ],
  "model": "flai-embeddings",
  "usage": {"prompt_tokens": 0, "total_tokens": 0}
}
```

- `input` accepts a single non-empty string or an array of non-empty strings.
  Token arrays are not supported.
- `encoding_format` accepts `float` (default) and `base64` (little-endian
  float32, the OpenAI wire format).
- `model`, `dimensions` and `user` are accepted and ignored: FLAI has one
  embedding model and one dimension.
- The request creates no chat session and stores no message. It still goes
  through the queue and takes the GPU lock, so a large batch waits behind a
  running generation instead of competing with it.
- `usage` is reported as zeros: the embedding path does not bill tokens.

---

## `GET /v1/models`

Returns the FLAI capability ids as OpenAI model objects. The list is a
capability catalogue, not a set of weights: any of these ids may be handled by
the router, and passing one as `model` changes nothing.

```json
{"object": "list", "data": [{"id": "flai-chat", "object": "model", "owned_by": "flai", "description": "Router-selected chat"}]}
```

## `GET /v1/flai/me`

Identity and feature flags for the key owner — useful for a setup screen that
wants to know what the server supports before it builds a request.

```json
{
  "login": "valery",
  "service_class": 2,
  "is_admin": true,
  "language": "en",
  "response_style": "neutral",
  "capabilities": {
    "chat_completions": true,
    "streaming": true,
    "embeddings": true,
    "audio_speech": false,
    "audio_transcriptions": false,
    "images": false,
    "videos": false,
    "documents": false,
    "rlm": false,
    "tools": false,
    "response_format_json_schema": false
  }
}
```

---

## Errors

Every error uses the OpenAI envelope, and every message starts with `⚠️ `:

```json
{
  "error": {
    "message": "⚠️ Chat session not found",
    "type": "invalid_request_error",
    "param": null,
    "code": "session_not_found"
  }
}
```

Messages are localized to the API user's language, so an English-speaking user
gets English errors and a Russian-speaking user gets Russian ones. The machine
readable part is always the `code`.

| Status | `type` | `code` | Cause |
|---|---|---|---|
| 400 | `invalid_request_error` | `invalid_body` | Body is not a JSON object |
| 400 | `invalid_request_error` | `invalid_request` | `messages` or a content part is malformed |
| 400 | `invalid_request_error` | `invalid_user`, `invalid_session_id`, `invalid_stream` | Field has the wrong type |
| 400 | `invalid_request_error` | `invalid_input`, `invalid_encoding_format` | Embeddings field is not usable |
| 401 | `invalid_request_error` | `invalid_api_key` | Missing, malformed, unknown or revoked key |
| 404 | `invalid_request_error` | `session_not_found` | `metadata.session_id` belongs to another user |
| 405 | `invalid_request_error` | `method_not_allowed` | Wrong HTTP method |
| 408 | `server_error` | `task_timeout` | Task still running after `API_SYNC_MAX_WAIT`; it was **not** cancelled |
| 429 | `rate_limit_error` | `rate_limit_exceeded` | The key owner's request budget is spent |
| 429 | `rate_limit_error` | `too_many_requests` | `API_MAX_CONCURRENT_WAITS` requests already waiting (`Retry-After: 1`) |
| 500 | `server_error` | `task_failed` | Chat task failed |
| 502 | `server_error` | `task_failed` | Embedding task failed or returned no vectors |
| 503 | `service_unavailable` | `api_disabled` | `API_ENABLED=false` on the server |

In a stream, a task error arrives as an SSE `error` object followed by
`data: [DONE]`, not as an HTTP status — the status line is already sent by
then.

---

## Timeouts, queueing and limits

FLAI runs on a single consumer GPU. Requests are serialized, and a request that
needs a model which is not resident pays the model load first. Keep this in
mind when choosing client timeouts:

- A short chat answer is typically a few seconds; a reasoning answer, a web
  search, an image or a video takes longer.
- `API_SYNC_MAX_WAIT` (default `600` seconds) bounds a synchronous wait. On
  expiry the API returns `408` with the task id in the message. The task keeps
  running and its answer is still saved to the conversation — a `408` means
  "ask again later", not "it failed".
- Keep the client timeout above `API_SYNC_MAX_WAIT`, or set
  `API_SYNC_MAX_WAIT` below the gunicorn timeout (900 s) so the server always
  answers first. This is the default arrangement.
- Concurrent clients do not run in parallel on the GPU; they queue. Do not
  retry aggressively on `408` — a slow answer is usually already in progress.
- `API_MAX_CONCURRENT_WAITS` (default `64`) caps how many `/v1` requests may
  wait for a queued task at the same time. A waiter holds a request thread for
  up to `API_SYNC_MAX_WAIT`, so a burst of clients would otherwise pile up on a
  single-worker server. When the cap is reached the API answers `429` with
  `Retry-After: 1` and `code: too_many_requests` **before** enqueueing, so
  nothing is left running. A request that is rejected, times out or finishes
  returns its slot immediately. Async jobs (image, video) do not hold a slot
  after they are enqueued.
- `API_RATE_LIMIT` (default `60 per minute;1000 per hour`) bounds
  `POST /v1/chat/completions` and `POST /v1/embeddings`. The budget is counted
  per API key **owner**, not per key or per IP: several keys of one user share
  it, and one noisy client cannot spend another user's quota. A spent budget
  returns `429 rate_limit_exceeded` before any task is queued, so nothing is
  left running in the background. `GET /v1/models` and `GET /v1/flai/me` are
  not limited.

### Browser clients (CORS)

A page in a browser can only read a cross-origin response when the server sends
`Access-Control-Allow-Origin`, so `/v1` answers preflights for the origins you
allow:

```
API_CORS_ORIGINS=https://home.example, https://tools.example
```

- The list is empty by default, which keeps the API closed to browsers.
- Origins are matched **exactly**, never by prefix, so
  `https://home.example` does not admit `https://home.example.evil.example`.
- Headers are added only to `/v1/*`. The web UI is never given an allow header,
  so the list cannot be used to read the chat interface cross-origin.
- The allowed methods are `GET, POST, DELETE, OPTIONS` and the allowed request
  headers are `Authorization` and `Content-Type`; the preflight is cached for
  600 s.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `API_ENABLED` | `true` | Master switch. When `false`, every `/v1` request returns `503 api_disabled`. |
| `API_SYNC_MAX_WAIT` | `600` | Seconds a synchronous or streaming request waits for its task. |
| `API_RATE_LIMIT` | `60 per minute;1000 per hour` | Request budget per key owner for chat completions and embeddings. Reported by `GET /v1/flai/me`. |
| `API_MAX_CONCURRENT_WAITS` | `64` | Requests that may wait for a queued task at once. Reported by `GET /v1/flai/me`. |
| `API_CORS_ORIGINS` | _(empty)_ | Comma-separated exact origins allowed to call `/v1` from a browser. Empty means no browser access. |

All five are documented in `.env.example`. Changing them requires a container
restart.

---

## Security notes

- Keys are stored as SHA-256 digests; a database dump does not reveal a usable
  key.
- Every request re-validates the session owner before touching a session, so a
  guessed `session_id` cannot reach another user's conversation.
- The API runs in the same process as the web app and is CSRF-exempt (it
  cannot carry a session cookie), but a Bearer token is required for
  everything. Its own budget is per key owner, separate from the web login
  limiter.
- Put the API behind a reverse proxy with TLS before exposing it outside your
  LAN: keys travel in the `Authorization` header in plain text over HTTP.
