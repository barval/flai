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
cd /home/GIT/GITEA/BARVAL-MY/flai/services/room-snapshot-api
git clone https://github.com/barval/room-snapshot-api.git room-snapshot-api
```

### 2. Configure cameras

Edit `room-snapshot-api/config/cameras.conf`:

```conf
# Format: code=ip:port:name
spa=192.168.131.101:554/stream1:Bedroom
gos=192.168.131.102:554/stream1:Living room
kab=192.168.131.103:554/stream1:Office
```

Set `RTSP_AUTH` (camera login:password) in `room-snapshot-api/.env`:

```bash
cp room-snapshot-api/.env.example room-snapshot-api/.env
nano room-snapshot-api/.env
```

```env
RTSP_AUTH="admin:password"
FLASK_DEBUG=false
```

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
- Checks/creates the `flai_flai_network` Docker network (for local mode)
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
```

Restart FLAI:

```bash
docker compose -f docker-compose.gpu.yml restart web
```

## Ports

| Port  | Purpose                            |
|-------|------------------------------------|
| `5000`| Container internal port (Docker network) |
| `5005`| External port (host mapping)       |

> **Important**: When connecting from the FLAI container via the Docker network use port **5000**.
> When connecting from the host (curl, browser) — port **5005**.

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
curl http://localhost:5005/health

# List of cameras
curl http://localhost:5005/rooms

# Save a snapshot
curl http://localhost:5005/snapshot/gos -o gos.jpg

# Camera info
curl http://localhost:5005/rooms/gos
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
4. Check the `cameras.conf` format (code=ip:port:name)

### FLAI does not see cameras

1. Make sure `CAMERA_API_URL` in FLAI's `.env` points to port **5000** (not 5005)
2. Check that the containers are on the same Docker network:
   ```bash
   docker network inspect flai_flai_network
   ```
3. Check from the FLAI container:
   ```bash
   docker exec flai-web python3 -c "import requests; r=requests.get('http://flai-room-snapshot-api:5000/health'); print(r.json())"
   ```

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
- [ ] `FLASK_DEBUG=false`
- [ ] Firewall: access to port 5005 only from the FLAI server
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
