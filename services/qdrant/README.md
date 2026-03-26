# Qdrant - Standalone Deployment

## Quick Start

```bash
# 1. Generate API key
QDRANT_API_KEY=$(openssl rand -hex 32)
echo "QDRANT_API_KEY=$QDRANT_API_KEY" > .env

# 2. Copy environment file (merge with generated key)
cp .env.example .env.temp
echo "QDRANT_API_KEY=$QDRANT_API_KEY" >> .env
mv .env .env.backup
cat .env.temp .env > .env.final
mv .env.final .env
rm .env.temp .env.backup

# 3. Start the service
docker-compose up -d

# 4. Check status
docker-compose logs -f