#!/usr/bin/env bash
# SuperLocalMemory startup script
# No daemon needed — SLM CLI runs per-request with --sync.
# The HTTP proxy handles all CLI communication.

set +e

# Ensure base SLM setup is done (creates config.json, downloads embedding model on first use)
if [ ! -f /root/.superlocalmemory/config.json ]; then
    slm setup --non-interactive --mode a > /tmp/slm_setup.log 2>&1
    echo "SLM setup: $?" > /tmp/slm_setup_status
fi

# Pre-download embedding model in background (~500MB, so first --sync is fast)
slm warmup > /tmp/slm_warmup.log 2>&1 &

# Start the HTTP proxy in foreground (keeps container alive)
exec python3 /app/slm_http.py
