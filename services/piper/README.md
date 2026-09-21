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

The service loads voices from `voice_map` in `app.py` — the model files must exist in `services/piper/models/` (mounted as `/app/models`):

| Language | Gender | Model | Files |
|----------|--------|-------|-------|
| Russian | male | `ru_RU-dmitri-medium` | `.onnx` + `.onnx.json` |
| Russian | female | `ru_RU-irina-medium` | `.onnx` + `.onnx.json` |
| English | male | `en_US-ryan-medium` | `.onnx` + `.onnx.json` |
| English | female | `en_US-ljspeech-medium` | `.onnx` + `.onnx.json` |

The automated download script fetches exactly these four voices:

```bash
./download-voices.sh        # writes .onnx/.onnx.json into services/piper/piper_models/
```

Manual download (raw files, no archives — Piper needs `.onnx` + `.onnx.json` side by side):

```bash
# Russian male
wget -P piper_models \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json

# Russian female
wget -P piper_models \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json

# English male
wget -P piper_models \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json

# English female
wget -P piper_models \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json
```

## Configuration

### Environment Variables (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPER_PORT` | `8888` | Port for TTS API |
| `PIPER_MODEL_DIR` | `/app/models` | Directory with `.onnx`/`.onnx.json` voice files (set in the container) |

The voice per request is chosen by `language` + `gender` in the API payload (see `voice_map` in `app.py`) — there are no per-voice env variables. An unknown combination falls back to the male voice of the same language.

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

The endpoint takes `text`, optional `language` (`en` default) and `gender` (`male` default), and returns **MP3** audio:

```bash
curl -X POST http://localhost:8888/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Привет, мир", "language": "ru", "gender": "male"}' \
  --output speech.mp3
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

2. Verify model format (flat files, `.onnx` + matching `.onnx.json` side by side):
   ```bash
   ls piper_models/ | grep ru_RU-dmitri
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
