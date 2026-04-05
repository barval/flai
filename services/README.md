# FLAI Services - Distributed Deployment Guide

## Overview

This directory contains configuration and deployment scripts for running FLAI services separately across multiple servers. This allows for distributed load balancing and scaling of individual components.

## Available Services

| Service | Port | Description | GPU Required |
|---------|------|-------------|--------------|
| [Ollama](ollama/) | 11434 | LLM inference | Optional (recommended) |
| [Automatic1111](automatic1111/) | 7860 | Image generation | Yes (NVIDIA) |
| [Whisper](openai-whisper/) | 9000 | Speech transcription | Optional |
| [Piper](piper/) | 8888 | Text-to-speech | No |
| [Qdrant](qdrant/) | 6333/6334 | Vector database | No |
| [Room Snapshot API](room-snapshot-api/) | 5005 | Camera integration | No |

## Deployment Scenarios

### Scenario 1: All-in-One (Default)

All services run on a single server using `docker-compose.all.yml` in the project root.

**Best for:**
- Development/testing
- Small deployments
- Limited hardware

### Scenario 2: Distributed Deployment

Services are distributed across multiple servers based on resource requirements.

**Example Architecture:**

| Server | Role | Components | GPU |
|--------|------|-----------|-----|
| Server 1 | Web App | FLAI, Redis | No |
| Server 2 | LLM | Ollama | Recommended |
| Server 3 | Images | Automatic1111 | NVIDIA |
| Server 4 | Storage | Qdrant, Piper | No |
| Server 5 | Voice | Whisper ASR | Optional |
| Server 6 | Camera | Room Snapshot API | No |

All services connect to the FLAI Web App via REST API over the local network.

**Best for:**
- Production deployments
- High load environments
- Resource optimization

## Quick Start

### Step 1: Choose Deployment Mode

For each service, decide:
- **Local**: Run on same server as FLAI web app
- **Remote**: Run on separate server

### Step 2: Deploy Services

```bash
# Example: Deploy Ollama on separate server
cd services/ollama
docker-compose up -d

# Pull required models
docker exec flai-ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
```

### Step 3: Configure FLAI Application

Update `.env` in the main FLAI directory:

```bash
# Ollama (remote server at 192.168.1.100)
OLLAMA_URL=http://192.168.1.100:11434

# Automatic1111 (remote server at 192.168.1.101)
AUTOMATIC1111_URL=http://192.168.1.101:7860

# Whisper (local deployment)
WHISPER_API_URL=http://flai-whisper:9000/asr

# Piper (local deployment)
PIPER_URL=http://flai-piper:8888/tts

# Qdrant (remote server at 192.168.1.102)
QDRANT_URL=http://192.168.1.102:6333
QDRANT_API_KEY=your-secret-key

# Camera API (local deployment)
CAMERA_API_URL=http://flai-room-snapshot-api:5005
```

### Step 4: Restart FLAI Application

```bash
cd /path/to/flai
docker-compose -f docker-compose.all.yml up -d web
```

## Network Configuration

### Firewall Rules

For each remote service, configure firewall to allow access only from FLAI server:

```bash
# On remote server
sudo ufw allow from <flai-server-ip> to any port <service-port>
```

### Required Ports

| Service | Port | Protocol | Direction |
|---------|------|----------|-----------|
| Ollama | 11434 | TCP | FLAI → Ollama |
| Automatic1111 | 7860 | TCP | FLAI → SD |
| Whisper | 9000 | TCP | FLAI → Whisper |
| Piper | 8888 | TCP | FLAI → Piper |
| Qdrant REST | 6333 | TCP | FLAI → Qdrant |
| Qdrant gRPC | 6334 | TCP | FLAI → Qdrant |
| Camera API | 5005 | TCP | FLAI → Camera |

## Security Considerations

### Production Checklist

- [ ] Set strong API keys for all services
- [ ] Configure firewall rules (whitelist only FLAI server IP)
- [ ] Use HTTPS via reverse proxy for external access
- [ ] Enable service health checks
- [ ] Set up monitoring and alerting
- [ ] Regular security updates
- [ ] Backup configurations and data

### API Keys

Generate secure API keys:

```bash
openssl rand -hex 32
```

Update service `.env` files with generated keys.

### Network Isolation

Use separate Docker networks for service isolation:

```bash
# Create network on each server
docker network create flai_network
```

## Monitoring

### Health Check Endpoints

| Service | Endpoint |
|---------|----------|
| Ollama | `http://<host>:11434/api/tags` |
| Automatic1111 | `http://<host>:7860/sdapi/v1/progress` |
| Whisper | `http://<host>:9000/` |
| Piper | `http://<host>:8888/health` |
| Qdrant | `http://<host>:6333/` |
| Camera API | `http://<host>:5005/health` |

### Monitoring Script

```bash
#!/bin/bash
# check-services.sh

services=(
    "http://ollama-server:11434/api/tags"
    "http://sd-server:7860/sdapi/v1/progress"
    "http://whisper-server:9000/"
    "http://piper-server:8888/health"
    "http://qdrant-server:6333/"
    "http://camera-server:5005/health"
)

for url in "${services[@]}"; do
    if curl -f -s "$url" > /dev/null; then
        echo "✅ $url"
    else
        echo "❌ $url"
    fi
done
```

## Backup and Recovery

### Backup Scripts

```bash
#!/bin/bash
# backup-all.sh

BACKUP_DIR="./backups/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Backup Ollama models
tar -czf "$BACKUP_DIR/ollama-models.tar.gz" ollama/models

# Backup SD models
tar -czf "$BACKUP_DIR/sd-models.tar.gz" automatic1111/models

# Backup Piper voices
tar -czf "$BACKUP_DIR/piper-models.tar.gz" piper/piper_models

# Backup Qdrant data
tar -czf "$BACKUP_DIR/qdrant-storage.tar.gz" qdrant/qdrant_storage

# Backup configurations
tar -czf "$BACKUP_DIR/configs.tar.gz" */.env

echo "Backup completed: $BACKUP_DIR"
```

### Recovery

```bash
# Stop services
docker-compose down

# Restore from backup
tar -xzf backups/20260401/ollama-models.tar.gz
tar -xzf backups/20260401/sd-models.tar.gz
tar -xzf backups/20260401/piper-models.tar.gz
tar -xzf backups/20260401/qdrant-storage.tar.gz

# Restart services
docker-compose up -d
```

## Troubleshooting

### Connection Issues

1. **Check service status:**
   ```bash
   docker-compose ps
   ```

2. **Test connectivity:**
   ```bash
   curl http://<service-host>:<port>/health
   ```

3. **Check firewall:**
   ```bash
   sudo ufw status
   ```

4. **View logs:**
   ```bash
   docker-compose logs -f <service-name>
   ```

### Performance Issues

1. **Check resource usage:**
   ```bash
   docker stats
   ```

2. **Monitor GPU usage:**
   ```bash
   nvidia-smi
   ```

3. **Check network latency:**
   ```bash
   ping <service-host>
   ```

## Support

For issues and questions:
- Check individual service README files
- Review FLAI main documentation
- Check Docker logs for errors
