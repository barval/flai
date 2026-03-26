#!/bin/bash
# services/ollama/ollama-setup.sh
# Download models listed in models.txt

set -e

CONTAINER_NAME="flai-ollama"
MODELS_FILE="models.txt"

echo "Waiting for Ollama to be ready..."
sleep 10

echo "Downloading models..."
while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    model_name=$(echo "$line" | xargs)
    echo "Pulling: $model_name"
    docker exec "$CONTAINER_NAME" ollama pull "$model_name" || echo "Failed: $model_name"
done < "$MODELS_FILE"

echo "Done!"
docker exec "$CONTAINER_NAME" ollama list