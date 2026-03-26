#!/bin/bash
# services/qdrant/deploy-qdrant.sh
# Deploy Qdrant with auto-generated API key

set -e

echo "Generating secure API key..."
API_KEY=$(openssl rand -hex 32)

echo "Creating .env file..."
cat > .env << EOF
QDRANT_HTTP_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_API_KEY=${API_KEY}
EOF

echo "Starting Qdrant..."
docker-compose up -d

echo "Waiting for Qdrant to be ready..."
sleep 10

echo "Testing connection..."
curl -s -H "api-key: ${API_KEY}" http://localhost:6333/ || echo "Connection test failed"

echo ""
echo "=========================================="
echo "Qdrant deployed successfully!"
echo "HTTP API:  http://localhost:6333"
echo "gRPC API:  localhost:6334"
echo "API Key:   ${API_KEY}"
echo ""
echo "Save this API key to your FLAI .env file:"
echo "QDRANT_URL=http://<server-ip>:6333"
echo "QDRANT_API_KEY=${API_KEY}"
echo "=========================================="