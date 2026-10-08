# Configuration

## Environment Variables (.env)

**Required:**
```bash
SECRET_KEY=your_secret_key_here      # Flask session secret
TIMEZONE=Europe/Moscow              # Your timezone
DATABASE_URL=postgresql://flai:flai_password@postgres:5432/flai  # PostgreSQL connection
```

**Backend Mode:**
```bash
LLAMACP_BACKEND=llama-swap    # 'llama-swap' (default, recommended) or 'llamacpp' (direct)
LLAMA_SWAP_URL=http://flai-llamaswap:8080  # llama-swap endpoint
```

**Service URLs:**
```bash
SD_WRAPPER_URL=http://flai-sd:7861          # sd-wrapper HTTP API (sd-cli wrapper)
WHISPER_API_URL=http://flai-whisper:9000/asr
PIPER_URL=http://flai-piper:8888/tts
QDRANT_URL=http://flai-qdrant:6333
QDRANT_API_KEY=your_qdrant_api_key
CAMERA_API_URL=http://flai-room-snapshot-api:5000
 LTX_VIDEO_WRAPPER_URL=http://flai-ltxvideo:7872  # LTX-Video video generation
 SLM_URL=http://flai-slm:8766                      # SuperLocalMemory long-term memory
 ```

**Image & Video Defaults:**
```bash
SD_CPP_DEFAULT_WIDTH=1024
SD_CPP_DEFAULT_HEIGHT=1024
SD_CPP_DEFAULT_CFG_SCALE=1.0    # 1.0 for flow-matching models (Z_image_turbo)
SD_CPP_DEFAULT_STEPS=10         # 10 for Z_image_turbo
SD_CPP_TIMEOUT=900              # 15 min for editing
MAX_IMAGE_SIZE=1536             # Resize uploaded images to 1536px on longest side
MAX_IMAGE_SIZE_MULTI=1024       # Per-image long side when several images are attached
MAX_CHAT_IMAGES=4               # Max images per chat message (also for deep analysis)
MAX_IMAGE_SIZE_MB=5             # Max upload size of an attached image
MAX_DOCUMENT_SIZE_MB=25         # Max upload size of a document
MAX_VOICE_SIZE_MB=5             # Max upload size of a voice note
LTX_VIDEO_TIMEOUT=600           # Max video generation time (seconds)
OCR_TIME_BUDGET_S=1800          # Scanned-PDF OCR budget per document task (0 = unlimited)
```

**Service Retry Settings:**
```bash
SERVICE_RETRY_ATTEMPTS=5
SERVICE_RETRY_DELAY=2
```

**Session Security:**
```bash
# Set to true ONLY when deployed behind reverse proxy (nginx) with HTTPS enabled
HTTPS_ENABLED=false
# Session lifetime is fixed at 8 hours in code (app/config.py) — no env override.
```

**RLM Deep Analysis:**
```bash
RLM_ENABLED=true                # Enable the deep-analysis toggle
RLM_ACTOR_MODEL=reasoning       # Model used for the actor loop
RLM_MAX_STEPS=18                # Hard ceiling for the actor loop; per-host allowance is hardware-derived (24 GB+→18, 16 GB→12, 12 GB→10, 8 GB→8, CPU/<8 GB→6)
RLM_TASK_TIMEOUT=0              # Wall-clock deadline (seconds): 0 = auto from step budget (CPU ~3x), -1 disables
RLM_CODE_TIMEOUT=15             # Per python-snippet timeout in the sandbox (seconds)
RLM_OBS_TRUNC=4000              # Max chars of one tool observation fed back to the model
RLM_SUB_MAX_TOKENS=1024         # Max tokens of an llm() sub-model call
RLM_WEB_MAX_FETCHES=5           # Hard ceiling for web_fetch callbacks per analysis
RLM_MAX_CORPUS_CHARS=50000000   # Corpus size cap — aborts oversized analyses before they load (OOM guard)
```

**Router:**
```bash
ROUTER_CONTEXT_MESSAGES=6    # Recent chat messages fed to the router for context-aware classification
ROUTER_CONTEXT_MSG_CHARS=240 # Max chars of one message in the router's session-context digest
ROUTER_SLM_FACTS=2           # Long-term-memory facts added to the router context
```

**Redis Queue:**
```bash
REDIS_RESULT_TTL=3600
QUEUE_MAX_WAIT_TIME=300
```

**Debug:**
```bash
DEBUG_API_ENABLED=false   # Set to 'true' only for development/testing
```

**Public API (/v1):**
```bash
API_RATE_LIMIT=60 per minute;1000 per hour  # Per-key-owner request budget for chat, embeddings, audio, media, files, RLM, sessions, tasks
API_MAX_CONCURRENT_WAITS=64                 # Max synchronous waiters before 429 with Retry-After
API_CORS_ORIGINS=                           # Comma-separated exact origins allowed to call /v1 from browser (empty = closed)
API_SYNC_MAX_WAIT=600                       # Seconds a sync/streaming request waits for its task (default 600)
```

**Model Hub:**
```bash
MODEL_HUB_MAX_FILE_GB=40              # Max single GGUF file size to allow (GB)
MODEL_HUB_FREE_MARGIN_GB=4            # Required free disk margin for downloads (GB)
MODEL_HUB_SEARCH_LIMIT=20             # Max results per Hugging Face search
MODEL_HUB_TIMEOUT_S=3600              # Download timeout (seconds)
HUGGINGFACE_TOKEN=                    # Optional: for private/gated repos
```

## Domain Access and HTTPS (Reverse Proxy)

By default the web interface is available at `http://<server-ip>:5000` — the web service publishes port `5000` (`"5000:5000"` in `docker-compose.gpu.yml` / `docker-compose.cpu.yml`) and Gunicorn listens on `0.0.0.0:5000`.

To serve FLAI under your own domain, put a reverse proxy (nginx, Caddy, Traefik) in front of it. The app trusts proxy headers (`ProxyFix`: `X-Forwarded-Proto`, `X-Forwarded-Host`, `X-Forwarded-For`), so redirects and `url_for` automatically pick up your domain and the HTTPS scheme.

**Step 1.** (optional) Close direct access to port 5000: in `docker-compose.gpu.yml` / `docker-compose.cpu.yml` change `"5000:5000"` to `"127.0.0.1:5000:5000"` and restart with `docker compose -f docker-compose.gpu.yml up -d flai-web`.

**Step 2.** In `.env`:
```bash
# Set to 'true' ONLY behind an HTTPS reverse proxy (nginx) — enables the Secure flag for session cookies
HTTPS_ENABLED=true
```

**Step 3.** Example nginx configuration (`/etc/nginx/sites-available/flai`):
```nginx
server {
    listen 80;
    server_name flai.example.com;

    # Must be >= MAX_CONTENT_LENGTH_MB from .env (default 75 MB)
    client_max_body_size 200m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;      # required for SSE response streaming
        proxy_read_timeout 900s;  # >= gunicorn timeout (900s)
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/flai /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# HTTPS:
sudo certbot --nginx -d flai.example.com
```

**Step 4.** Alternatively, the same with **Caddy** (`Caddyfile`) — certificates are issued automatically:
```
flai.example.com {
    reverse_proxy 127.0.0.1:5000
}
```

You can now open `https://flai.example.com`.

## Docker Configuration

**Gunicorn Settings (gunicorn_config.py):**

Configuration is loaded from `gunicorn_config.py`, not inline CLI args.

| Setting | Value | Reason |
|---------|-------|--------|
| workers | 1 | Single gunicorn worker — fixes `_gpu_lock` race condition (threading.Lock is per-process) |
| worker_class | gevent | Async I/O-optimized worker for concurrent connections |
| timeout | 900s | Accommodates long operations (image editing up to 15 min) |
| graceful_timeout | 30s | Graceful worker shutdown |
| keepalive | 5s | Connection reuse for health checks |

## Docker Compose Profiles

```bash
# Start all services (multimodal + images + voice + RAG + video + long-term memory + web search)
docker compose -f docker-compose.gpu.yml --profile with-image-gen --profile with-voice-piper --profile with-rag --profile with-video --profile with-slm --profile with-search up -d

# Chat + voice only (Piper backend; use with-voice-kokoro for Kokoro)
docker compose -f docker-compose.gpu.yml --profile with-voice-piper up -d

# Video generation
docker compose -f docker-compose.gpu.yml --profile with-video up -d

# Long-term memory (SuperLocalMemory)
docker compose -f docker-compose.gpu.yml --profile with-slm up -d

# Chat only (no images, no voice, no video)
docker compose -f docker-compose.gpu.yml up -d

# Stop all services
docker compose -f docker-compose.gpu.yml down --remove-orphans

# View logs
docker compose -f docker-compose.gpu.yml logs -f web
```

---
