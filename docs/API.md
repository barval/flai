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

## Interactive documentation

| What | Where |
|---|---|
| Swagger UI (browse and try every endpoint) | `/v1/docs` |
| OpenAPI 3.0.3 document (machine readable) | `/v1/openapi.json` |

Both are served from `docs/openapi-v1.yaml` and work **without a Bearer key**,
because a spec is not data — but every operation they document requires one.
The UI ships with the server, so the page renders offline with no CDN.

> `docs/openapi.yaml` is a separate, older document for the internal web/admin
> API. It predates `/v1` and is not served; use `/v1/openapi.json` for
> integrations.

This file is the prose contract; the two above are generated from the same
endpoint list and are kept in sync by `tests/test_api_docs.py`.

---

## Status

| Capability | Endpoint | State |
|---|---|---|
| API keys | Web UI, `/api/api-keys/*` | Shipped |
| Chat completion (sync) | `POST /v1/chat/completions` | Shipped |
| Chat completion (SSE stream) | `POST /v1/chat/completions` with `stream: true` | Shipped |
| Chat completion (async) | `POST /v1/flai/chat/async` | Shipped |
| Embeddings | `POST /v1/embeddings` | Shipped |
| Model list | `GET /v1/models` | Shipped |
| Identity and capabilities | `GET /v1/flai/me` | Shipped |
| Speech synthesis | `POST /v1/audio/speech` | Shipped |
| Audio transcription | `POST /v1/audio/transcriptions` | Shipped |
| Task list/status/cancel | `/v1/flai/tasks*` | Shipped |
| Task media download | `GET /v1/flai/tasks/{task_id}/content` | Shipped |
| Files and documents | `/v1/files*`, `/v1/flai/documents*` | Shipped |
| Image generation/edits | `POST /v1/images/generations`, `POST /v1/images/edits` | Shipped |
| Video generation | `POST /v1/videos`, `GET /v1/videos/{task_id}` | Shipped |
| Deep analysis (RLM) | `POST /v1/flai/rlm` | Shipped |
| Sessions and history | `/v1/flai/sessions*` | Shipped |
| Interactive reference | `/v1/docs`, `/v1/openapi.json` | Shipped |
| Documents, deep analysis UI | Web UI only | Web UI |

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
and will not fetch an image from the internet on your behalf. Up to
`MAX_CHAT_IMAGES` (4) image parts are kept in one request (parity with the web
multi-attachment chat): they are answered in one joint vision call, with a
per-image fallback when the joint call fails. A request with more than the cap
is rejected with `400`.

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

## `POST /v1/flai/chat/async`

Fire-and-poll chat: the request is queued exactly like `/v1/chat/completions`
and answered immediately with a task id, so a client never holds an open HTTP
connection while the GPU queue works.

```bash
curl http://localhost:5000/v1/flai/chat/async \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"user": "client-42", "messages": [{"role": "user", "content": "Compare the contracts"}]}'
```

```json
HTTP/1.1 202 Accepted
{
  "id": "b6f1d0a2-4c77-4a1e-9c3f-2f1d8e5b7a10",
  "object": "flai.task",
  "status": "queued",
  "poll_url": "/v1/flai/tasks/b6f1d0a2-4c77-4a1e-9c3f-2f1d8e5b7a10"
}
```

Poll `poll_url` with the same Bearer key:

```bash
curl http://localhost:5000/v1/flai/tasks/b6f1d0a2-4c77-4a1e-9c3f-2f1d8e5b7a10 \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"
```

```json
{
  "id": "b6f1d0a2-4c77-4a1e-9c3f-2f1d8e5b7a10",
  "object": "flai.task",
  "status": "completed",
  "result": {
    "response": "Both contracts cover the same period…",
    "usage": {"prompt_tokens": 1120, "completion_tokens": 340}
  }
}
```

- The body is the same as `/v1/chat/completions`: `messages`, plus `user` or
  `metadata.session_id` for session continuity. `model`, `stream`, `tools` and
  `response_format` are accepted and ignored (see the table above).
- The user turn is persisted and queued before the response is sent, so the
  answer also appears in the web UI thread for that session.
- `status` moves through `queued` → `processing` → `completed` / `error` /
  `cancelled`; `result.response` holds the answer text.
- Cancellation is `POST /v1/flai/tasks/{task_id}/cancel`. A chat task has no
  generated file, so `/content` does not apply to it.
- No synchronous wait slot is taken: the request returns before the model runs,
  so this endpoint does not consume `API_MAX_CONCURRENT_WAITS`.

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

## `POST /v1/audio/speech`

Synthesize text with the configured TTS backend. Kokoro returns WAV and Piper
returns MP3; unsupported `response_format` values are rejected rather than
silently relabeling one format as another.

```bash
curl http://localhost:5000/v1/audio/speech \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"input":"Hello from FLAI"}' \
  --remote-name --remote-header-name
```

| Field | Behavior |
|---|---|
| `input` | Required, non-empty text. |
| `voice` | Optional voice name passed to Kokoro; Piper may ignore it. |
| `language`, `gender` | Optional FLAI extensions; language accepts `ru` or `en`, gender accepts `male` or `female`; defaults come from the API key owner's profile. |
| `response_format` | Defaults to the backend's native format (`wav` for Kokoro, `mp3` for Piper); other formats return `400 invalid_response_format`. |
| `model`, `speed` | Accepted and ignored; FLAI does not select a TTS model or apply speed adjustment. |

The response is the audio bytes with the MIME type returned by the TTS service
and a matching attachment name (`speech.wav` or `speech.mp3`). Speech is
stateless and does not write a message into the user's chat. An unavailable
TTS module returns `503`; a synthesis failure returns `500`.

---

## `POST /v1/audio/transcriptions`

Upload an audio file as `multipart/form-data`. Transcription is queued through
the existing queue and Whisper service, but unlike a web voice message it does
not create a chat session, save a transcript message or publish the result to
the shared user SSE channel.

```bash
curl http://localhost:5000/v1/audio/transcriptions \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -F "file=@meeting.wav" \
  -F "response_format=json"
```

| Field | Behavior |
|---|---|
| `file` | Required multipart audio upload; validated by the configured audio module. |
| `response_format` | `json` (default, returns `{"text":"..."}`) or `text` (plain UTF-8 text). `srt`, `vtt` and `verbose_json` are unsupported because the Whisper module does not provide timestamps. |
| `language` | Optional FLAI extension (`ru` or `en`); defaults to the API key owner's language. |
| `model` | Accepted and ignored; FLAI uses the configured Whisper service. |

Files above Flask's `MAX_CONTENT_LENGTH` receive `413 request_too_large` in the
OpenAI error envelope. A transcription that exceeds `API_SYNC_MAX_WAIT` returns
`408 task_timeout`; its queue task continues, but this stateless endpoint does
not persist the transcript into chat.

---

## Asynchronous task status and cancellation

Long-running FLAI operations can be polled through the owner-scoped task
registry. A task id is not an authorization token: every read and cancel checks
that the ID was registered to the authenticated API-key owner.

```bash
# List the most recent API tasks (limit is clamped to 1–100; default 20)
curl http://localhost:5000/v1/flai/tasks?limit=20 \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"

# Inspect one task
curl http://localhost:5000/v1/flai/tasks/your-task-id \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"

# Request cancellation of an active task
curl -X POST http://localhost:5000/v1/flai/tasks/your-task-id/cancel \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"
```

- `GET /v1/flai/tasks` returns recent tasks for this API-key owner only.
- `GET /v1/flai/tasks/{task_id}` returns `queued`, `processing`, `completed` or
  `error`. A task requeued to a new queue ID is followed and the child ID is
  returned with `parent_task_id`; a child with conflicting ownership metadata
  is never read.
- Pending task responses use `position: null` when the queue does not expose an
  exact position for that task.
- Results are allowlisted metadata; filesystem paths, uploaded file bytes,
  tokens and raw queue payloads are never returned.
- A requeued child task is followed only when its owner/session/endpoint record
  matches the parent. Conflicting or incomplete child metadata fails closed.
- The task-list index is capped to the most recent 500 task IDs per API user;
  older task records still expire with `REDIS_RESULT_TTL`.
- `POST /v1/flai/tasks/{task_id}/cancel` returns `{"status":"cancelling"}`
  only for an active processing task. Completed, failed and still-queued tasks
  return `409 task_not_cancellable` because the existing queue cannot remove a
  queued item safely.
- Unknown or another user's task ID returns `404 task_not_found`, avoiding an
  ownership oracle.

The task registry is stored in Redis for `REDIS_RESULT_TTL` seconds, matching
queue result retention. Task list/status/cancel endpoints use the API owner's
`API_RATE_LIMIT` budget.

Example task response:

```json
{
  "id": "a1d5b76a-...",
  "object": "flai.task",
  "status": "completed",
  "endpoint": "/v1/videos",
  "created_at": 1780152000.5,
  "result": {"response": "⚠️ ...", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
}
```

---

## Asynchronous image and video generation

Images and videos are produced by the same serialized GPU queue as web chat:
the endpoints enqueue a task and return `202 Accepted` with a poll URL. No
model generation ever runs inside the HTTP request, and `API_MAX_CONCURRENT_WAITS`
is not consumed by an async job after it is enqueued.

```bash
# Generate an image from a prompt
curl http://localhost:5000/v1/images/generations \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a lighthouse at dusk", "model": "flai-image"}'

# Generate in the style of attached examples ("draw something similar")
curl http://localhost:5000/v1/images/generations \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
        "prompt": "a lighthouse at dusk",
        "image_references": [
          {"image_url": {"url": "data:image/png;base64,/9j/4AAQSk..."}},
          {"image_url": {"url": "data:image/png;base64,R0lGODlh..."}}
        ]
      }'

# Edit an uploaded image (multipart)
curl http://localhost:5000/v1/images/edits \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -F image=@photo.jpg \
  -F prompt="make it winter"

# Start a video generation
curl http://localhost:5000/v1/videos \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "waves on a beach", "width": 768, "height": 512, "num_frames": 96, "frame_rate": 12}'
```

`image_references` (optional, up to `MAX_CHAT_IMAGES`) mirrors the web
«draw something similar» flow: each example is described by the multimodal
model and the descriptions steer the SD prompt. Only inline `data:` URLs are
accepted; more references than the cap return `400`.

- `response_format` must be `url` (default). `b64_json` is rejected with
  `400 invalid_request_error`: the payload is stored server-side, so clients
  download it via the content URL instead of receiving megabytes inline.
- Image edits require a multipart `image` upload and a non-empty `prompt`;
  path-like file references are never accepted.
- Video options are validated against the configured `VideoModule` instead of
  being passed through. Any key other than `prompt`, `model`, `user` and
  `metadata` must be an **integer** inside its range; anything else is rejected
  with `400 invalid_video_options`.

  | Option | Range | Default |
  |---|---|---|
  | `width`, `height` | 256–1024, multiples of 32 | 768, 512 |
  | `num_frames` | 9–240 | 240 |
  | `frame_rate` | 6, 12, 16 or 24 | 24 |
  | `seed` | -1 … 2³¹−1 | model default |

  Total pixels are capped at `768x512x240` and the duration at 10 s
  (`num_frames <= frame_rate * 10`); exceeding either is `400`.
- When the image or video module is missing or unavailable the endpoints
  return `503 service_unavailable`.
- Every response carries the prompt in the task record, and the conversation
  turn (prompt + generated media) is persisted into the API session history.

The task id in the `202` body is polled through the owner-scoped endpoints
above. A completed media task adds `content_url` to the response, and video
tasks mirror it as `url` (OpenAI video-job shape):

```json
{
  "id": "a1d5b76a-...",
  "object": "flai.task",
  "status": "completed",
  "endpoint": "/v1/images/generations",
  "content_url": "/v1/flai/tasks/a1d5b76a-.../content",
  "result": {"response": "Image generated", "usage": {"prompt_tokens": 12, "completion_tokens": 0}}
}
```

### `GET /v1/flai/tasks/{task_id}/content`

Downloads the generated media for a completed task. The check is layered: the
task must belong to the authenticated owner, and the underlying message is
fetched with a join proving the session belongs to the same owner. The file is
resolved with `realpath` under `UPLOAD_FOLDER`; caller-provided paths are never
accepted, so traversal or a foreign task returns `404 task_not_found` without a
filesystem oracle. The response is `send_file` bytes with the stored MIME type
and a safe filename.

---

## Files and documents

User documents are exposed both through the OpenAI Files-shaped `/v1/files`
routes and FLAI aliases under `/v1/flai/documents`. Both families call the same
handlers, so ownership, quota and validation behavior is identical.

```bash
# List your documents
curl http://localhost:5000/v1/files -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"

# Upload a document (multipart). It is validated, stored and queued for indexing.
curl http://localhost:5000/v1/files \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -F file=@report.pdf

# Fetch metadata / download / delete
curl http://localhost:5000/v1/files/{file_id} -H "Authorization: Bearer flai_..."
curl http://localhost:5000/v1/files/{file_id}/content -H "Authorization: Bearer flai_..." -o report.pdf
curl -X DELETE http://localhost:5000/v1/files/{file_id} -H "Authorization: Bearer flai_..."
```

- List/metadata entries contain only `id`, `object: "file"`, `filename`,
  `bytes`, `created_at`, `purpose: "assistants"` and the FLAI extension
  `index_status` (`pending`, `processing`, `indexed`, `error`).
- Uploads reuse the web validation chain: allowed extension + magic bytes,
  `MAX_DOCUMENT_SIZE_MB` (`413`), per-user document quota (`413`). Images are
  auto-resized and converted exactly like chat uploads.
- Storage names are UUIDs under `DOCUMENTS_FOLDER/<login>/`; responses never
  contain filesystem paths.
- The download endpoint resolves the stored relative path with `realpath`
  containment; foreign and unknown ids return the same `404 file_not_found`.
- Deletion removes the RAG index entry (when the RAG module is available), the
  stored file and the database record for the owner only.

---

## Deep analysis (RLM)

`POST /v1/flai/rlm` runs an explicit deep-analysis task over caller-owned
documents (and optionally attached images) through the same GPU-serialized
queue the web chat uses. It requires `RLM_ENABLED`; otherwise the endpoint
returns `403 rlm_disabled` and `GET /v1/flai/me` reports `rlm: false`.

```bash
curl http://localhost:5000/v1/flai/rlm \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
        "session_id": "3f2b1c4d-...",
        "doc_ids": ["a1b2c3d4-..."],
        "question": "Compare the risk sections of these contracts",
        "images": [{"image_url": {"url": "data:image/png;base64,/9j/4AAQSk..."}}]
      }'
```

- `session_id` must be an owned session (`404 session_not_found` otherwise)
  and every `doc_ids` entry must be owned (`404 document_not_found`); at least
  one document or an attached image is required (`400`).
- Images (up to `MAX_CHAT_IMAGES`): a multipart variant accepts `files` (the
  legacy single `file` field still works), a JSON body accepts `images` as
  OpenAI-style `image_url` parts with inline `data:` URLs. Every image passes
  the same quota and resize cap as chat uploads and joins the analysis corpus
  as its own «Image (name)» document, exactly like the web deep analysis.
- The user turn is saved into the API session history, and the response is
  `202 Accepted` with `task_id`, `position` and `user_message_id`.
- Poll the returned `task_id` through `/v1/flai/tasks/{task_id}` — the task is
  registered in the owner index automatically.

---

## Sessions and history

API conversations are ordinary FLAI chat sessions; these endpoints let a client
list, create and read them without touching the web UI's last-session pointer.

```bash
# List sessions owned by the key owner
curl http://localhost:5000/v1/flai/sessions -H "Authorization: Bearer flai_..."

# Create a session (optional title; defaults to a localized "New session")
curl -X POST http://localhost:5000/v1/flai/sessions \
  -H "Authorization: Bearer flai_..." \
  -H "Content-Type: application/json" \
  -d '{"title": "Support bot"}'

# Read history (limit clamped to 1–200, offset >= 0)
curl "http://localhost:5000/v1/flai/sessions/{session_id}/messages?limit=50&offset=0" \
  -H "Authorization: Bearer flai_..."
```

- List responses are `{"object": "list", "data": [{id, title, created_at}, ...]}`.
- Message responses mirror the web API shape:
  `{"messages": [...], "limit", "offset", "has_more"}`; file payloads already
  stored on disk are redacted from `file_data` by the shared DB helper.
- Foreign or unknown session ids return `404 session_not_found`.
- `/v1` responses never set a cookie: API identity is request-local.

---

## `GET /v1/models`

Returns the FLAI capability ids as OpenAI model objects. The list is a
capability catalogue, not a set of weights: any of these ids may be handled by
the router, and passing one as `model` changes nothing.

```json
{"object": "list", "data": [{"id": "flai-chat", "object": "model", "owned_by": "flai", "description": "Router-selected chat"}, {"id": "flai-tts", "object": "model", "owned_by": "flai", "description": "Speech synthesis"}, {"id": "flai-stt", "object": "model", "owned_by": "flai", "description": "Speech transcription"}]}
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
    "audio_speech": true,
    "audio_transcriptions": true,
    "images": true,
    "videos": true,
    "documents": true,
    "rlm": true,
    "tools": false,
    "response_format_json_schema": false
  }
}
```

The two audio flags are true only when the configured TTS/audio modules are
available; `flai-tts` and `flai-stt` are capability identifiers in the model
list, not selectable weights. `images` and `videos` are true only when the
image/video module **and** the multimodal model (used for prompt enrichment)
are both available.

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
| 400 | `invalid_request_error` | `invalid_limit` | Task list limit is not an integer |
| 401 | `invalid_request_error` | `invalid_api_key` | Missing, malformed, unknown or revoked key |
| 404 | `invalid_request_error` | `session_not_found` | `metadata.session_id` belongs to another user |
| 404 | `invalid_request_error` | `task_not_found` | Task ID is unknown or belongs to another API user |
| 405 | `invalid_request_error` | `method_not_allowed` | Wrong HTTP method |
| 408 | `server_error` | `task_timeout` | Task still running after `API_SYNC_MAX_WAIT`; it was **not** cancelled |
| 409 | `invalid_request_error` | `task_not_cancellable` | Task is terminal or still queued |
| 413 | `invalid_request_error` | `request_too_large` | Upload exceeds Flask `MAX_CONTENT_LENGTH` |
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
  running and is not cancelled; chat answers are still saved to the
  conversation, while stateless endpoints do not create chat messages.
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
  `POST /v1/chat/completions`, `POST /v1/embeddings`, `/v1/audio/*`,
  `/v1/images/*`, `/v1/videos*`, `/v1/flai/tasks*`, `/v1/flai/chat/async`,
  `/v1/files*`, `/v1/flai/documents*`, `POST /v1/flai/rlm` and
  `/v1/flai/sessions*`.
  The budget is counted
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
- The allowed methods are `GET, POST, OPTIONS` and the allowed request
  headers are `Authorization` and `Content-Type`; the preflight is cached for
  600 s.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `API_ENABLED` | `true` | Master switch. When `false`, every `/v1` request returns `503 api_disabled`. |
| `API_SYNC_MAX_WAIT` | `600` | Seconds a synchronous or streaming request waits for its task. |
| `API_RATE_LIMIT` | `60 per minute;1000 per hour` | Request budget per key owner for chat, embeddings, audio, media generation, files/documents, deep analysis, sessions and task-control endpoints. Reported by `GET /v1/flai/me`. |
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
