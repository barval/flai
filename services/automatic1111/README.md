# Automatic1111 (Stable Diffusion) - Standalone Deployment

## Overview

This directory contains configuration for deploying Automatic1111 Stable Diffusion service separately from the main FLAI application.

## Quick Start

```bash
# 1. Create models directory (DO NOT DELETE - contains SD checkpoints)
mkdir -p models

# 2. Download Stable Diffusion checkpoint
# Example: RealVisXL V4.0
wget -O models/RealVisXL_V4.0.safetensors \
  https://huggingface.co/SG161222/RealVisXL_V4.0/resolve/main/RealVisXL_V4.0.safetensors

# 3. Copy environment file
cp .env.example .env

# 4. Start the service
docker-compose up -d

# 5. Check status
docker-compose logs -f
```

## Configuration

### Environment Variables (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `SD_PORT` | `7860` | Port for Automatic1111 API |
| `SD_MODEL` | `RealVisXL_V4.0.safetensors` | Default SD checkpoint |
| `SD_COMMANDLINE_ARGS` | `--api --listen` | Additional command line arguments |

### Ports

- **7860** - Automatic1111 API and Web UI

## Network Configuration

### For Remote Deployment

If deploying Automatic1111 on a separate server:

1. **Open port 7860** in firewall:
   ```bash
   sudo ufw allow 7860/tcp
   ```

2. **Update FLAI application .env**:
   ```bash
   AUTOMATIC1111_URL=http://<sd-server-ip>:7860
   ```

3. **Secure with firewall rules** (recommended):
   ```bash
   sudo ufw allow from <flai-server-ip> to any port 7860
   ```

## GPU Requirements

### Minimum Requirements
- NVIDIA GPU with 6GB VRAM
- 16GB system RAM

### Recommended Requirements
- NVIDIA GPU with 8GB+ VRAM
- 32GB system RAM
- SSD storage

## API Usage

### Generate Image

```bash
curl -X POST http://localhost:7860/sdapi/v1/txt2img \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "a beautiful landscape",
    "steps": 20,
    "width": 512,
    "height": 512
  }'
```

### Check Progress

```bash
curl http://localhost:7860/sdapi/v1/progress
```

## Health Check

```bash
curl http://localhost:7860/sdapi/v1/progress
```

Expected response:
```json
{"progress": 0.0, ...}
```

## Monitoring

### View Logs
```bash
docker-compose logs -f
```

### Check Resource Usage
```bash
docker stats flai-sd
```

### Check GPU Usage
```bash
docker exec flai-sd nvidia-smi
```

## Backup Models

To backup model checkpoints:

```bash
# Stop the service
docker-compose down

# Backup models directory
tar -czf sd-models-backup.tar.gz models

# Restart service
docker-compose up -d
```

## Troubleshooting

### Out of Memory (OOM)

1. Use smaller models or lower resolution
2. Add `--medvram` or `--lowvram` to SD_COMMANDLINE_ARGS
3. Reduce batch size

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
   curl http://localhost:7860/sdapi/v1/progress
   ```

### Slow Generation

1. Use GPU acceleration
2. Reduce image resolution
3. Use fewer sampling steps
4. Use optimized models (LCM, Turbo)

## Security Notes

- **Do not expose Automatic1111 API to public internet** without authentication
- Use firewall rules to restrict access to trusted IPs only
- Consider using reverse proxy (nginx) with authentication for production
- Automatic1111 Web UI has no built-in authentication - use with caution
