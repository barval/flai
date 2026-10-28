# Room Snapshot API — Deployment Guide

## Overview

The **Room Snapshot API** service provides HTTP access to IP camera snapshots for the FLAI application.
The service code lives in the `room-snapshot-api/` subdirectory (cloned from a separate repository).

**Two deployment options:**

1. **Local** — on the same server as FLAI
2. **Remote** — on a separate server

## File Structure

```
services/room-snapshot-api/
├── README.md                           ← This file
├── deploy.sh                           ← Deployment script
├── docker-compose-local.yml            ← Local docker-compose
├── docker-compose-remote.yml           ← Remote docker-compose
└── room-snapshot-api/                  ← Service code (repository clone)
    ├── Dockerfile
    ├── app/
    ├── config/cameras.conf             ← Camera configuration
    ├── requirements.txt
    └── .env
```

## Quick Start

### 1. Clone the service code (if not already cloned)

```bash
cd flai/services/room-snapshot-api
git clone --branch v1.3 https://github.com/barval/room-snapshot-api.git room-snapshot-api
```

### 2. Configure cameras

Edit `room-snapshot-api/config/cameras.conf`:

```conf
# Format: code=ip:port/path:name
spa=192.168.1.101:554/stream1:Bedroom
gos=192.168.1.102:554/stream1:Living room
kab=192.168.1.103:554/stream1:Office
```

Copy the environment template and fill the shared key:

```bash
cp room-snapshot-api/.env.example room-snapshot-api/.env
nano room-snapshot-api/.env
```

```env
# Every REST and MCP request needs this key; FLAI uses the same value.
API_KEY="generate-a-strong-key"
# Default room-label language: en or ru
DEFAULT_LANG="ru"
# RTSP credentials shared by cameras without a cameras.auth record
RTSP_AUTH="admin:password"
# MCP server port inside the container (compose publishes 5006)
MCP_PORT="8000"
FLASK_DEBUG=false
```

Credentials that differ per camera go to `room-snapshot-api/config/cameras.auth`
(`code=user:password`), English labels to `config/rooms.i18n.json`:

```json
{ "kuh": { "ru": "Кухня", "en": "Kitchen" } }
```

Both files are optional and are ignored by git.

### 3. Deploy

The `deploy.sh` script automates the whole process:

```bash
# Local deployment (on the same server as FLAI)
./deploy.sh local

# Remote deployment (separate server)
./deploy.sh remote
```

Useful commands:

```bash
./deploy.sh status     # Show service status
./deploy.sh logs       # View logs in real time
./deploy.sh restart    # Restart
./deploy.sh stop       # Stop
```

The script automatically:
- Creates `.env` with a secure `SECRET_KEY` (if missing or default)
- Checks/creates the Docker network (for local mode; FLAI's compose declares it as `flai_network`)
- Builds and starts the container
- Waits for the service to be ready (up to 15 attempts, `/health` check)
- Prints the status, JSON health response, and further instructions

### 4. Connect to FLAI

In the main FLAI application `.env`:

```bash
# Local deployment (Docker network, internal port 5000)
CAMERA_API_URL=http://flai-room-snapshot-api:5000
CAMERA_ENABLED=true
CAMERA_API_TIMEOUT=15
CAMERA_CHECK_INTERVAL=30
# Must match API_KEY in the service .env
CAMERA_API_KEY=generate-a-strong-key
# MCP is another container of the same compose; 5006 is the host-side publish
CAMERA_MCP_URL=http://flai-room-snapshot-api-mcp:8000/mcp
CAMERA_SNAPSHOT_WIDTH=384
CAMERA_PREVIEW_WORKERS=4
```

Restart FLAI:

```bash
docker compose -p flai -f docker-compose.gpu.yml up -d --no-deps web
```

## Ports

| Port  | Purpose                            |
|-------|------------------------------------|
| `5000`| REST container port (Docker network) |
| `5005`| REST host mapping                  |
| `8000`| MCP container port (Docker network)  |
| `5006`| MCP host mapping                   |

> **Important**: When connecting from the FLAI container via the Docker network use port **5000** for REST and **8000** for MCP.
> From the host (curl, browser) — **5005** and **5006**.

Every endpoint requires the shared key:

```bash
export API_KEY="$(grep '^API_KEY=' room-snapshot-api/.env | cut -d= -f2- | tr -d '\"')"
```

## API Endpoints

| Endpoint    | Method | Description                 |
|-------------|--------|-----------------------------|
| `/health`   | GET    | Health check                |
| `/rooms`    | GET    | List of available cameras   |
| `/rooms/<code>` | GET | Info about a specific camera |
| `/snapshot/<code>` | GET | Camera snapshot (JPEG) |
| `/info`     | GET    | API info                    |

### Examples

```bash
# Health check
curl -H "X-API-Key: $API_KEY" http://localhost:5005/health

# List of cameras (language via ?lang= or Accept-Language)
curl -H "X-API-Key: $API_KEY" -H "Accept-Language: ru" http://localhost:5005/rooms

# Save a snapshot
curl -H "X-API-Key: $API_KEY" "http://localhost:5005/snapshot/gos?width=640" -o gos.jpg

# Camera info
curl -H "X-API-Key: $API_KEY" http://localhost:5005/rooms/gos
```

## Monitoring

```bash
# Logs
./deploy.sh logs

# Container status
docker ps --filter name=room-snapshot-api

# Resource usage
docker stats flai-room-snapshot-api
```

## Troubleshooting

### Container does not start

```bash
# Logs
docker compose -f docker-compose-local.yml logs --tail=50

# Check camera config exists
ls -la room-snapshot-api/config/cameras.conf

# Check .env
cat room-snapshot-api/.env
```

### Cannot get a snapshot

1. Check camera reachability: `ping CAMERA_IP`
2. Check the logs: `./deploy.sh logs`
3. Verify `RTSP_AUTH` in `.env`
4. Check the `cameras.conf` format (code=ip:port/path:name)

### FLAI does not see cameras

1. Make sure `CAMERA_API_URL` in FLAI's `.env` points to port **5000** (not 5005)
2. Check that the containers are on the same Docker network (FLAI's compose names it `flai_network`):
   ```bash
   docker network inspect flai_network
   ```
3. Check from the FLAI container:
   ```bash
   docker exec flai-web python3 -c "import os, requests; print(requests.get('http://flai-room-snapshot-api:5000/rooms', headers={'X-API-Key': os.environ['CAMERA_API_KEY']}).json())"
   ```
   A `401` means `CAMERA_API_KEY` in FLAI does not match `API_KEY` in the service `.env`.

### Service does not respond on port 5005

```bash
# Check container
docker ps --filter name=room-snapshot-api

# Check port mapping
docker port flai-room-snapshot-api

# Check health
curl http://localhost:5005/health
```

## Security

### Production checklist

- [ ] Secure `SECRET_KEY` (generated automatically by deploy.sh)
- [ ] `API_KEY` is set, strong and matches FLAI's `CAMERA_API_KEY`
- [ ] `FLASK_DEBUG=false`
- [ ] Firewall: access to port 5005 only from the FLAI server
- [ ] Firewall: MCP port 5006 is not exposed publicly
- [ ] HTTPS via reverse proxy (nginx)
- [ ] Up-to-date camera firmware
- [ ] Regular security updates

### Firewall (remote deployment)

```bash
# Allow access only from the FLAI server
sudo ufw allow from <flai-server-ip> to any port 5005

# Deny everyone else
sudo ufw deny 5005/tcp
```

## Configuration Backup

```bash
# Backup camera config
tar -czf camera-config-backup.tar.gz room-snapshot-api/config/

# Backup .env
cp room-snapshot-api/.env room-snapshot-api/.env.backup
```
