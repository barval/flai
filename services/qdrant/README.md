# Qdrant - Standalone Deployment

## Overview

This directory contains configuration for deploying Qdrant vector database separately from the main FLAI application. Used for RAG (Retrieval-Augmented Generation) functionality.

## Quick Start

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Generate API key (recommended for production)
openssl rand -hex 32

# 3. Add API key to .env
QDRANT_API_KEY=<your-generated-key>

# 4. Start the service
docker-compose up -d

# 5. Check status
docker-compose logs -f
```

## Configuration

### Environment Variables (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_PORT` | `6333` | REST API port |
| `QDRANT_GRPC_PORT` | `6334` | gRPC API port |
| `QDRANT_API_KEY` | (none) | API key for authentication |
| `QDRANT_STORAGE` | `./qdrant_storage` | Data storage directory |

### Ports

- **6333** - REST API endpoint
- **6334** - gRPC API endpoint

## Network Configuration

### For Remote Deployment

If deploying Qdrant on a separate server:

1. **Open ports** in firewall:
   ```bash
   sudo ufw allow 6333/tcp
   sudo ufw allow 6334/tcp
   ```

2. **Update FLAI application .env**:
   ```bash
   QDRANT_URL=http://<qdrant-server-ip>:6333
   QDRANT_API_KEY=<your-api-key>
   ```

3. **Secure with firewall rules** (recommended):
   ```bash
   sudo ufw allow from <flai-server-ip> to any port 6333
   sudo ufw allow from <flai-server-ip> to any port 6334
   ```

## API Usage

### Check Health

```bash
curl http://localhost:6333/
```

### Create Collection

```bash
curl -X PUT http://localhost:6333/collections/documents \
  -H "Content-Type: application/json" \
  -d '{
    "vectors": {
      "size": 1024,
      "distance": "Cosine"
    }
  }'
```

### Health Check

```bash
curl http://localhost:6333/cluster
```

Expected response:
```json
{"result": {...}}
```

## Monitoring

### View Logs
```bash
docker-compose logs -f
```

### Check Resource Usage
```bash
docker stats flai-qdrant
```

### Access Qdrant Dashboard

Open in browser: `http://localhost:6333/dashboard`

## Data Backup

To backup vector database:

```bash
# Stop the service
docker-compose down

# Backup storage directory
tar -czf qdrant-backup.tar.gz qdrant_storage

# Restart service
docker-compose up -d
```

## Security Notes

### Production Checklist

- [ ] Set strong `QDRANT_API_KEY`
- [ ] Enable firewall rules
- [ ] Use HTTPS via reverse proxy
- [ ] Regular backups
- [ ] Monitor disk usage

### API Key Authentication

All API requests must include the API key:

```bash
curl http://localhost:6333/collections \
  -H "api-key: your-api-key"
```

### Firewall Configuration

```bash
# Allow only from FLAI application server
sudo ufw allow from <flai-server-ip> to any port 6333
sudo ufw allow from <flai-server-ip> to any port 6334

# Deny all other access
sudo ufw deny 6333/tcp
sudo ufw deny 6334/tcp
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
   curl http://localhost:6333/
   ```

### Out of Disk Space

1. Check storage usage:
   ```bash
   du -sh qdrant_storage
   ```

2. Delete unused collections via API

3. Increase disk space

### High Memory Usage

Reduce vector size or use quantization in collection configuration.

## Performance Tuning

### For Large Datasets

1. Use SSD storage
2. Increase RAM (16GB+ recommended)
3. Enable HNSW index optimization
4. Use quantization for vectors

### For High Load

1. Use separate server for Qdrant
2. Enable gRPC for better performance
3. Configure connection pooling in FLAI app
