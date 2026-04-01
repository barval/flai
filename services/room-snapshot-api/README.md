# Room Snapshot API - Deployment Guide

## Overview

This directory contains deployment configurations for the Room Snapshot API service. Two deployment options are available:

1. **Local Deployment** - Run on the same server as FLAI application
2. **Remote Deployment** - Run on a separate server for distributed load

## Deployment Options

### Option 1: Local Deployment (Same Server)

Use this option when running the camera API on the same server as the main FLAI application.

```bash
# Run local deployment
./deploy.sh local
```

**Characteristics:**
- Simpler network configuration
- Lower latency
- Shares server resources with FLAI
- Uses internal Docker network

### Option 2: Remote Deployment (Separate Server)

Use this option when running the camera API on a different server.

```bash
# Run remote deployment
./deploy.sh remote
```

**Characteristics:**
- Distributed load across servers
- Independent scaling
- Requires network configuration
- Uses external IP communication

## Configuration

### Environment Variables (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `CAMERA_API_PORT` | `5005` | API server port |
| `SECRET_KEY` | (required) | Flask secret key |
| `FLASK_DEBUG` | `false` | Debug mode |
| `CAMERA_CONFIG_DIR` | `./config` | Camera configuration directory |

### Ports

- **5005** - Camera API endpoint

## Network Configuration

### Local Deployment

No additional network configuration required. Services communicate via Docker internal network.

### Remote Deployment

1. **Open port 5005** in firewall:
   ```bash
   sudo ufw allow 5005/tcp
   ```

2. **Update FLAI application .env**:
   ```bash
   CAMERA_API_URL=http://<camera-server-ip>:5005
   CAMERA_ENABLED=true
   ```

3. **Secure with firewall rules**:
   ```bash
   sudo ufw allow from <flai-server-ip> to any port 5005
   ```

## Deployment Scripts

### Local Deployment

```bash
#!/bin/bash
# deploy-local.sh - Deploy on same server as FLAI

docker-compose -f docker-compose-local.yml up -d
```

### Remote Deployment

```bash
#!/bin/bash
# deploy-remote.sh - Deploy on separate server

docker-compose -f docker-compose-remote.yml up -d
```

## Health Check

```bash
curl http://localhost:5005/health
```

Expected response:
```json
{"status": "ok"}
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/rooms` | GET | List available rooms/cameras |
| `/snapshot/<room_code>` | GET | Get snapshot from room |
| `/api/snapshot/<room_code>` | GET | Alternative snapshot endpoint |

## Monitoring

### View Logs
```bash
docker-compose logs -f
```

### Check Resource Usage
```bash
docker stats room-snapshot-api
```

### Test Camera Access
```bash
curl http://localhost:5005/rooms
curl http://localhost:5005/snapshot/gos
```

## Troubleshooting

### Connection Refused

1. Check if container is running:
   ```bash
   docker-compose ps
   ```

2. Check firewall rules:
   ```bash
   sudo ufw status
   ```

3. Test local connectivity:
   ```bash
   curl http://localhost:5005/health
   ```

### Camera Not Available

1. Check camera configuration in `config/` directory
2. Verify camera network connectivity
3. Check camera credentials

### High Latency

1. Use local deployment if possible
2. Check network latency between servers
3. Optimize camera resolution

## Security Notes

### Production Checklist

- [ ] Set strong `SECRET_KEY`
- [ ] Enable firewall rules
- [ ] Use HTTPS via reverse proxy
- [ ] Secure camera credentials
- [ ] Regular security updates

### Firewall Configuration

```bash
# For remote deployment - allow only from FLAI server
sudo ufw allow from <flai-server-ip> to any port 5005

# Deny all other access
sudo ufw deny 5005/tcp
```

### Camera Security

- Store camera credentials securely
- Use encrypted connections to cameras (HTTPS/RTSPS)
- Regularly update camera firmware
- Limit camera access to necessary rooms only

## Backup Configuration

To backup camera configuration:

```bash
# Backup config directory
tar -czf camera-config-backup.tar.gz config

# Backup .env file
cp .env .env.backup
```

## Integration with FLAI

### Update FLAI .env

```bash
# For local deployment
CAMERA_API_URL=http://flai-room-snapshot-api:5005

# For remote deployment
CAMERA_API_URL=http://<camera-server-ip>:5005
```

### Verify Integration

1. Restart FLAI application
2. Check FLAI logs for camera module initialization
3. Test camera snapshot from FLAI interface
