# Ollama - Standalone Deployment

## Overview

This directory contains configuration for deploying Ollama service separately from the main FLAI application. Useful for distributed deployments across multiple servers.

## Quick Start

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Start the service
docker-compose up -d

# 3. Check status
docker-compose logs -f

# 4. Pull models (example)
docker exec flai-ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
docker exec flai-ollama ollama pull qwen3-vl:8b-instruct-q4_K_M
docker exec flai-ollama ollama pull gpt-oss:20b
docker exec flai-ollama ollama pull bge-m3:latest
```

## Configuration

### Environment Variables (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `0.0.0.0:11434` | Host and port to bind Ollama API |
| `OLLAMA_ORIGINS` | `*` | Allowed origins for CORS |
| `OLLAMA_MODELS` | `/root/.ollama/models` | Path to store models |

### Ports

- **11434** - Ollama API endpoint

## Network Configuration

### For Remote Deployment

If deploying Ollama on a separate server:

1. **Open port 11434** in firewall:
   ```bash
   sudo ufw allow 11434/tcp
   ```

2. **Update FLAI application .env**:
   ```bash
   OLLAMA_URL=http://<ollama-server-ip>:11434
   ```

3. **Secure with firewall rules** (recommended):
   ```bash
   # Allow only from FLAI application server
   sudo ufw allow from <flai-server-ip> to any port 11434
   ```

## GPU Acceleration

### NVIDIA GPU

For GPU acceleration, use the GPU-enabled docker-compose:

```bash
docker-compose -f docker-compose.gpu.yml up -d
```

**Requirements:**
- NVIDIA GPU with 8GB+ VRAM (recommended)
- NVIDIA Container Toolkit installed

### Verify GPU Access

```bash
docker exec flai-ollama nvidia-smi
```

## Model Recommendations

### Chat/Router (Fast responses)
```bash
docker exec flai-ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
```

### Multimodal (Image analysis)
```bash
docker exec flai-ollama ollama pull qwen3-vl:8b-instruct-q4_K_M
```

### Reasoning (Complex tasks)
```bash
docker exec flai-ollama ollama pull gpt-oss:20b
```

### Embedding (RAG search)
```bash
docker exec flai-ollama ollama pull bge-m3:latest
```

## Health Check

```bash
curl http://localhost:11434/api/tags
```

Expected response:
```json
{"models": [...]}
```

## Monitoring

### View Logs
```bash
docker-compose logs -f
```

### Check Resource Usage
```bash
docker stats flai-ollama
```

### List Models
```bash
docker exec flai-ollama ollama list
```

## Backup Models

To backup downloaded models:

```bash
# Stop the service
docker-compose down

# Backup models directory
tar -czf ollama-models-backup.tar.gz ollama/models

# Restart service
docker-compose up -d
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
   curl http://localhost:11434/api/tags
   ```

### Out of Memory

Reduce model size or increase server RAM. Use quantized models (q4_K_M).

### Slow Responses

1. Use GPU acceleration
2. Use smaller/quantized models
3. Increase OLLAMA_NUM_PARALLEL in .env

## Security Notes

- **Do not expose Ollama API to public internet** without authentication
- Use firewall rules to restrict access to trusted IPs only
- Consider using reverse proxy (nginx) with authentication for production
