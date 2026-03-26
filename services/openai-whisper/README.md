# Whisper ASR - Standalone Deployment

## Quick Start

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Edit .env for GPU acceleration (optional)
# ASR_DEVICE=cuda

# 3. Start the service
docker-compose up -d

# 4. Check status
docker-compose logs -f