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

## Camera Permissions
In Admin Panel → Users tab, assign camera codes:
`tam` (tambour/entry), `pri` (hallway), `kor` (corridor), `spa` (bedroom),
`kab` (office/study), `det` (children's), `gos` (living room), `kuh` (kitchen), `bal` (balcony)

---
