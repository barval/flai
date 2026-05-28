#!/usr/bin/env bash
# SuperLocalMemory startup script
# Starts the SLM daemon (holds embedding model in memory) then the HTTP proxy.

set +e

# Ensure base SLM setup is done (creates config.json, downloads embedding model)
if [ ! -f /root/.superlocalmemory/config.json ]; then
    slm setup --non-interactive --mode a > /tmp/slm_setup.log 2>&1
    echo "SLM setup: $?" > /tmp/slm_setup_status
fi

# Start the daemon (keeps MemoryEngine + embedding model in memory, ~300-800ms recall)
slm serve start > /tmp/slm_daemon.log 2>&1 &

# Wait for daemon to be ready (port 8765)
for i in $(seq 1 30); do
    if curl -s http://localhost:8765/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Start the HTTP proxy in foreground (proxies to daemon on localhost:8765)
exec python3 /app/slm_http.py
