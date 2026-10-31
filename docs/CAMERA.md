# Camera Integration


The camera module connects to a separate `room-snapshot-api` service. See [../services/README.md](../services/README.md) and [services/room-snapshot-api/README.md](../services/room-snapshot-api/README.md) for deployment guides.

## Camera Management (Admin Panel)
The admin panel includes a **Cameras** tab with full CRUD operations:
- **Sync** – import camera list from room-snapshot-api (`/rooms` endpoint)
- **Enable/Disable** – toggle individual cameras on/off
- **Thumbnail previews** – lazy-loaded camera snapshots with localStorage caching
- **Russian name recognition** – pymorphy3 morphological analysis generates all grammatical declensions (nominative, accusative, prepositional cases) for each room name, so the AI recognizes phrases like "show me the living room", "what is in the living room", "in the kitchen" etc.

Camera room data is stored in the `camera_rooms` database table (code, name_forms, enabled, sort_order).

## Configuration
```bash
CAMERA_API_URL=http://flai-room-snapshot-api:5000
CAMERA_ENABLED=true
CAMERA_API_TIMEOUT=15
CAMERA_CHECK_INTERVAL=30
```

## room-snapshot-api v1.4

The service authenticates every endpoint with a shared key and exposes the same
camera set over REST and MCP. FLAI sends the key on every call, asks for its own
language and reuses the service's snapshot cache.

| Setting | Meaning |
|---|---|
| `CAMERA_API_KEY` | Shared key for REST and MCP (`X-API-Key`). Empty means the service runs without auth — only acceptable behind a private network. |
| `CAMERA_MCP_URL` | Streamable-HTTP MCP endpoint (`http://host:5006/mcp`) — the seam for agent mode. |
| `CAMERA_SNAPSHOT_WIDTH` | Width requested for previews (default 384 px); the service resizes before capture. |
| `CAMERA_PREVIEW_WORKERS` | Cameras captured in parallel per refresh (default 4). |
| `MAX_CONCURRENT_CAPTURES` | ffmpeg processes the service may run at once (v1.4, default 4) — one client cannot spawn an unbounded number. |
| `RATE_LIMIT_PER_MINUTE` | Per-client request limit in the service (v1.4, default 0 = off). Behind a reverse proxy every client shares one bucket — limit in the proxy instead, or trust proxy headers knowingly. |

**Language.** The service defaults to English room labels, so FLAI sends
`Accept-Language` (and `?lang=`) with the interface language. A Russian
installation keeps Russian names without a per-installation translation file.

**Preview refresh.** `GET /admin/api/cameras/previews` captures every enabled
camera in one request: the module fans out with a bounded thread pool, the
service captures concurrently, and the response carries each thumbnail as a
data URL. One round trip instead of one request per row — the old table
serialised them through the browser's per-host connection limit while ffmpeg ran
sequentially on the other side.

**Caching.** Snapshots carry `ETag`/`Cache-Control`. The proxy endpoint
`/admin/api/cameras/<code>/proxy` forwards `If-None-Match` and answers `304 Not
Modified` without touching the camera, so reopening the tab does not re-capture.

**Addressing (v1.4).** A camera address may be IPv6: the service brackets it
(`rtsp://[2001:db8::1]:554/stream1`) and keeps the credentials URL-encoded. The
installation files are read from `CONFIG_DIR` (default `/app/config`), with
`CAMERAS_CONF`, `CAMERAS_AUTH` and `ROOMS_I18N` as individual overrides — FLAI
mounts `services/room-snapshot-api/room-snapshot-api/config` there.

**Every room in one request (v12.9).** The camera action used to answer "check
every room" with one snapshot: the router contract carried a single room code, so
a question about all rooms was answered from whichever room the model picked (and
the answer itself said "check the other cameras"). A camera request may now carry
`*` — the router emits `[-CAMERA-] *` when no room is named or every room is meant
— and the chat captures one snapshot per room the user may see, emits one camera
message each, then composes the answer over all of them in one multimodal call.
The number of captures is capped by `CAMERA_ALL_ROOMS_MAX` (default 12) and a
room with no signal is skipped and logged.

**Why the answer is composed per room, not from one batch (measured).** The first
version sent all snapshots in one multimodal call with the room order spelled
out; the model then placed cats in rooms where a single-image call correctly
said "none" — it contaminates images inside a batch and cannot reliably bind
"image 3" to "3) study". Two more findings from the same measurement: the
snapshots were 384×216 (the preview width), where a cat on the floor is a few
dozen pixels, and repeating the user's "check every room" wording on a single
image made the model answer "no cats here" for a kitchen it otherwise describes
as having one. The path now asks **one binary question per room** ("is there a
cat in this room? one word: yes or no") on a **1024 px** snapshot
(`CAMERA_ANALYSIS_WIDTH`) and groups the answers into "cats are in / no cats in /
not determined"; the user's wording is never passed to the per-room call.

**Tools (v12.9).** The tool loop got its camera surface: `list_rooms` returns the
codes and localized names the user may look at (offered only when the camera module
is available), and `camera_snapshot` returns a **description** of the snapshot
rather than the image — a base64 payload would consume the window and tell the
model nothing. The image itself still arrives as a chat message through the camera
action above.

**Health.** `/health` answers HTTP 200 in every state and reports
`healthy`/`degraded`/`unhealthy` with per-camera reachability. The Cameras tab
shows the online count next to the sync button.

**MCP (agent mode).** `GET /admin/api/cameras/mcp` connects to the MCP server
(JSON-RPC over streamable HTTP, SSE or JSON responses), lists its tools
(`list_rooms`, `get_snapshot`, `room_info`, `health`) and reports availability.
Nothing in the chat path uses it yet: the module behind `modules/cam_mcp.py` is
the tool surface the agent layer will call instead of talking RTSP or proxying
snapshots itself.

## Camera Permissions
In Admin Panel → Users tab, assign camera codes:
`tam` (tambour/entry), `pri` (hallway), `kor` (corridor), `spa` (bedroom),
`kab` (office/study), `det` (children's), `gos` (living room), `kuh` (kitchen), `bal` (balcony)

---
