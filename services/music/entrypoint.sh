#!/usr/bin/env bash
# Start the ACE-Step REST API. Models live in the /models volume and download
# on first start (~11 GB); the API serves on :8001.
set -euo pipefail

mkdir -p "${ACESTEP_CHECKPOINTS_DIR}" "${HF_HOME}"

export ACESTEP_API_HOST=0.0.0.0
exec /app/.venv/bin/acestep-api
