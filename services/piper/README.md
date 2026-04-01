# Piper TTS - Standalone Deployment

## Overview

This directory contains configuration for deploying Piper TTS service separately from the main FLAI application.

## Quick Start

```bash
# 1. Create models directory (DO NOT DELETE - contains voice models)
mkdir -p piper_models

# 2. Download voice models
# See download-voices.sh script for automated download

# 3. Copy environment file
cp .env.example .env

# 4. Start the service
docker-compose up -d

# 5. Check status
docker-compose logs -f
```

## Voice Models

### Download Russian Voices

```bash
# Male voice (ru_RU-dmitri)
wget -O piper_models/ru_RU-dmitri-medium.tar.gz \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.tar.gz

# Female voice (ru_RU-irina)
wget -O piper_models/ru_RU-irina-medium.tar.gz \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.tar.gz
```

### Download English Voices

```bash
# Male voice (en_US-lessac)
wget -O piper_models/en_US-lessac-medium.tar.gz \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.tar.gz

# Female voice (en_US-amy)
wget -O piper_models/en_US-amy-medium.tar.gz \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/amy/medium/en_GB-amy-medium.tar.gz
```

### Extract Models

```bash
cd piper_models
tar -xzf ru_RU-dmitri-medium.tar.gz
tar -xzf ru_RU-irina-medium.tar.gz
tar -xzf en_US-lessac-medium.tar.gz
tar -xzf en_GB-amy-medium.tar.gz
```

## Configuration

### Environment Variables (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPER_PORT` | `8888` | Port for TTS API |
| `PIPER_VOICE_RU_MALE` | `ru_RU-dmitri-medium` | Russian male voice model |
| `PIPER_VOICE_RU_FEMALE` | `ru_RU-irina-medium` | Russian female voice model |
| `PIPER_VOICE_EN_MALE` | `en_US-lessac-medium` | English male voice model |
| `PIPER_VOICE_EN_FEMALE` | `en_GB-amy-medium` | English female voice model |

### Ports

- **8888** - Piper TTS API endpoint

## Network Configuration

### For Remote Deployment

If deploying Piper on a separate server:

1. **Open port 8888** in firewall:
   ```bash
   sudo ufw allow 8888/tcp
   ```

2. **Update FLAI application .env**:
   ```bash
   PIPER_URL=http://<piper-server-ip>:8888/tts
   ```

3. **Secure with firewall rules** (recommended):
   ```bash
   sudo ufw allow from <flai-server-ip> to any port 8888
   ```

## API Usage

### Synthesize Speech

```bash
curl -X POST http://localhost:8888/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "speaker": "ru_RU-dmitri-medium"}' \
  --output speech.wav
```

## Health Check

```bash
curl http://localhost:8888/health
```

Expected response:
```json
{"status": "ok"}
```

## Monitoring

### View Logs
```bash
docker-compose logs -f
```

### Check Resource Usage
```bash
docker stats flai-piper
```

## Backup Voice Models

To backup voice models:

```bash
# Stop the service
docker-compose down

# Backup models directory
tar -czf piper-models-backup.tar.gz piper_models

# Restart service
docker-compose up -d
```

## Troubleshooting

### Model Not Found

1. Check if model files exist:
   ```bash
   ls -la piper_models/
   ```

2. Verify model format (should have .onnx and .json files):
   ```bash
   ls piper_models/ru_RU-dmitri-medium/
   ```

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
   curl http://localhost:8888/health
   ```

## Security Notes

- **Do not expose Piper API to public internet** without authentication
- Use firewall rules to restrict access to trusted IPs only
- Consider using reverse proxy (nginx) with authentication for production
