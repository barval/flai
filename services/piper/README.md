# Piper TTS - Standalone Deployment

## Quick Start

```bash
# 1. Create models directory
mkdir -p piper_models

# 2. Download voice models
# See download-voices.sh script

# 3. Copy environment file
cp .env.example .env

# 4. Start the service
docker-compose up -d

# 5. Check status
docker-compose logs -f