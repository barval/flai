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

## room-snapshot-api v1.3

The service authenticates every endpoint with a shared key and exposes the same
camera set over REST and MCP. FLAI sends the key on every call, asks for its own
language and reuses the service's snapshot cache.

| Setting | Meaning |
|---|---|
| `CAMERA_API_KEY` | Shared key for REST and MCP (`X-API-Key`). Empty means the service runs without auth — only acceptable behind a private network. |
| `CAMERA_MCP_URL` | Streamable-HTTP MCP endpoint (`http://host:5006/mcp`) — the seam for agent mode. |
| `CAMERA_SNAPSHOT_WIDTH` | Width requested for previews (default 384 px); the service resizes before capture. |
| `CAMERA_PREVIEW_WORKERS` | Cameras captured in parallel per refresh (default 4). |

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
