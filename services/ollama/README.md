# Ollama - Standalone Deployment

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