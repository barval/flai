# Whisper ASR - Standalone Deployment

## Overview

This directory contains configuration for deploying OpenAI Whisper ASR (Automatic Speech Recognition) service separately from the main FLAI application.

## Quick Start

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Start the service
docker-compose up -d

# 3. Check status
docker-compose logs -f
```

## Configuration

### Environment Variables (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_PORT` | `9000` | Port for Whisper ASR API |
| `ASR_MODEL` | `medium` | Whisper model size (tiny, base, small, medium, large) |
| `ASR_ENGINE` | `faster_whisper` | ASR engine to use |
| `ASR_DEVICE` | `cpu` | Device to run on (cpu, cuda) |

### Ports

- **9000** - Whisper ASR API endpoint

## Network Configuration

### For Remote Deployment

If deploying Whisper on a separate server:

1. **Open port 9000** in firewall:
   ```bash
   sudo ufw allow 9000/tcp
   ```

2. **Update FLAI application .env**:
   ```bash
   WHISPER_API_URL=http://<whisper-server-ip>:9000/asr
   ```

3. **Secure with firewall rules** (recommended):
   ```bash
   sudo ufw allow from <flai-server-ip> to any port 9000
   ```

## Model Sizes

| Model | Parameters | VRAM Required | Relative Speed |
|-------|------------|---------------|----------------|
| tiny | 39M | ~1GB | ~32x |
| base | 74M | ~1GB | ~16x |
| small | 244M | ~2GB | ~6x |
| medium | 769M | ~5GB | ~2x |
| large | 1550M | ~10GB | 1x |

### Recommended Models

- **CPU only**: `base` or `small`
- **GPU 4GB**: `small` or `medium`
- **GPU 8GB+**: `medium` or `large`

## API Usage

### Transcribe Audio

```bash
curl -X POST http://localhost:9000/asr \
  -F "audio_file=@recording.webm" \
  -F "output=json"
```

### Response Format

```json
{
  "text": "Hello world",
  "language": "en",
  "duration": 2.5
}
```

## Health Check

```bash
curl http://localhost:9000/
```

Expected response: HTTP 200 or 307 redirect

## Monitoring

### View Logs
```bash
docker-compose logs -f
```

### Check Resource Usage
```bash
docker stats flai-whisper
```

## GPU Acceleration

For GPU acceleration, edit `.env`:

```bash
ASR_DEVICE=cuda
ASR_ENGINE=faster_whisper
```

**Requirements:**
- NVIDIA GPU with 4GB+ VRAM
- NVIDIA Container Toolkit installed

## Troubleshooting

### Out of Memory

Use a smaller model:
```bash
ASR_MODEL=base
```

### Slow Transcription

1. Use GPU acceleration
2. Use smaller model (tiny, base)
3. Reduce audio quality/length

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
   curl http://localhost:9000/
   ```

## Security Notes

- **Do not expose Whisper API to public internet** without authentication
- Use firewall rules to restrict access to trusted IPs only
- Consider using reverse proxy (nginx) with authentication for production
