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
VRAM_WAIT_TIMEOUT_S=120                     # Seconds a busy GPU is waited out (ensure_vram_for / queue / video gate) before it is reported unavailable
VRAM_CO_RESIDENCY=auto                      # 'auto' keeps residents that fit next to the new model, 'off' unloads everything (pre-v12.8 behaviour)
VRAM_HEADROOM_MB=1200                       # VRAM kept free for prefill buffers when a co-residency decision is made
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
MAX_CHAT_AUDIO=4                # Max audio items per message (1 voice + 3 files, or 4 files)
MAX_IMAGE_SIZE_MB=5             # Max upload size of an attached image
MAX_DOCUMENT_SIZE_MB=25         # Max upload size of a document
MAX_VOICE_SIZE_MB=5             # Max upload size of a voice note
LTX_VIDEO_TIMEOUT=10800         # Max video generation time (seconds)
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

**Web Search and Crawling:**
```bash
SEARXNG_URL=http://flai-searxng:8080
SEARXNG_TIMEOUT=10
SEARXNG_MAX_RESULTS=7
SEARXNG_MAX_RESULTS_CHARS=5000
RAG_MAX_RESULTS_CHARS=5000
RAG_CHUNK_SIZE=500             # Characters per chunk
RAG_CHUNK_OVERLAP=50           # Overlap between consecutive chunks
RAG_CHUNK_STRATEGY=fixed       # fixed | recursive | structured
RAG_TOP_K=80                   # Chunks retrieved per query
RAG_CONTEXT_PERCENT=30         # Share of the context window given to retrieved chunks
RAG_HYBRID_ENABLED=true        # Merge dense and full-text retrieval with RRF (default on)
RAG_HYBRID_CANDIDATES=30       # Candidates per retriever before fusion
RAG_RRF_K=60                   # RRF constant
RAG_RERANK_ENABLED=false       # Cross-encoder reranking of the top candidates (CPU, no VRAM)
RAG_RERANK_CANDIDATES=10       # How many top candidates the reranker reorders
RAG_RERANK_TIMEOUT=10          # Seconds before reranking is skipped and the original order is kept
TAVILY_ENABLED=true               # Optional per-user Tavily keys; without a key the local SearXNG path is used
TAVILY_API_URL=https://api.tavily.com
TAVILY_TIMEOUT=20
TAVILY_USAGE_TIMEOUT=8
TAVILY_MAX_RESULTS=7
TAVILY_SEARCH_DEPTH=basic
TAVILY_ADMIN_CACHE_TTL=300     # How long an answered quota lookup is reused (Users tab and profile popup)
TAVILY_NEGATIVE_CACHE_TTL=60   # How long an unreachable quota endpoint is remembered before retrying
CRAWL_ENABLED=false               # Crawl4AI sidecar; enable with --with-crawler or this flag
CRAWL4AI_API_TOKEN=flai-crawler-token  # Compose token; the web app receives it as CRAWL_API_TOKEN
CRAWLER_URL=http://flai-crawler:11235
CRAWL_MAX_PAGES=50
CRAWL_MAX_DEPTH=3
CRAWL_TIMEOUT_S=300
CRAWL_PAGE_TIMEOUT_S=30
CRAWL_MAX_PAGE_CHARS=50000
CRAWL_MAX_TOTAL_CHARS=1000000
CRAWL_CONCURRENCY=2
```

**Background Tasks:**
```bash
TASKS_ENABLED=1                 # Executor kill switch (0 stops picking up new tasks; running steps finish or abort on cancel flags)
TASK_QUIET_PERIOD_S=15          # Sliding quiet window: a task step is preempted when any chat request arrives after this many quiet seconds
TASK_TOTAL_BUDGET_S=86400       # Pure execution budget per task (seconds); on expiry the task finishes with a final summary
TASK_STEP_MAX_ATTEMPTS=3        # Step attempts including corrective checkpoint retries, then the step is failed
TASK_MAX_REVISIONS=8            # Accepted plan rewrites per task; then the plan is final
TASK_MAX_STEPS=40               # Hard ceiling on the plan length
TASK_REPLAN_EVERY_N=3           # Ordinary passing media steps between plan reviews (failures/answers always review)
TASK_STEP_MAX_REPAIRS=3         # Image edits after generation attempts (local defects); then the best candidate is kept
TASK_CONTINUITY_ENABLED=1       # Visual continuity sheet before the first of 2+ generated images (0 disables)
TASK_CONTINUITY_MAX_CHARS=2000  # Character budget of the sheet (style + entity looks)
TASK_STEP_REDO_MAX=1            # Plan-review re-opens of a failed step (a verbatim repeated goal); 0 disables
TASK_POLL_INTERVAL_S=1          # Executor gate/cancel-flag polling interval
TASK_SUBTASK_POLL_CYCLES=10     # Cycles a task holds at one subtask before the liveness check
TASK_SUBTASK_ALIVE_UNKNOWN_MAX=3 # Cycles a failed liveness probe may hold a step before it is failed anyway
TASK_VRAM_WAIT_S=600            # How long one direct step waits for a busy GPU before it gives up
```

**Router:**
```bash
ROUTER_CONTEXT_MESSAGES=6    # Recent chat messages fed to the router for context-aware classification
ROUTER_CONTEXT_MSG_CHARS=240 # Max chars of one message in the router's session-context digest
CPU_REASONING_TTL=600         # CPU-only hosts: idle seconds before the reasoning model releases its RAM (unused when it fits next to the chat model)
CPU_RAM_RESERVE_MB=4096       # RAM kept free for the OS when deciding that both CPU models fit
MEMORY_EXTRACT_QUIET_S=45    # Memory extraction is interrupted by any chat/API request and waits this long before resuming
SLM_AUTO_EXTRACT=auto        # 'auto' extracts after each personal message where a GPU keeps the model resident; 'on' also on CPU
SLM_MIN_RECALL_SCORE=0.5     # Non-profile facts below this recall score are not injected
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

## Admin Panel Overrides

Almost every variable above can also be edited in **Admin Panel → Settings** and is then stored in the
database. The precedence is **database > `.env` > built-in default**: a value saved in the panel overrides
the environment, and the ↺ button next to a field drops the override so the `.env` value applies again.

- A saved value takes effect immediately, without a restart.
- Values are grouped (Search and crawl, Documents and RAG, Tasks, Models and context, Media generation,
  Deep analysis, Voice, Memory and history, API, Cameras, Service, Debug) and the search box filters by
  name or environment key.
- Secret values (`QDRANT_API_KEY`, `HUGGINGFACE_TOKEN`, `CRAWL_API_TOKEN`) are never sent back to the
  browser: only a mask is shown, and an empty field means "leave unchanged".
- Every change is written to `settings_audit` (who, when, old → new; secrets are redacted) and pruned
  after 45 days. Both tables are included in full backups.
- `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, the folder paths, `HTTPS_ENABLED`, `FLAI_PLATFORM` and the
  developer `DEBUG_JS` / `DEBUG_TRANSLATIONS` flags stay in `.env` only — they are needed before the
  database is reachable or define process behaviour.

## Deployment Script and Stored Overrides

`deploy.sh` (and its Russian twin `deploy-ru.sh`) still edit `.env`: `--with-search`,
`--with-crawler`, `--with-slm` and the voice backend flags comment or uncomment `SEARXNG_URL`,
`CRAWL_ENABLED`, `SLM_URL`, `KOKORO_URL` and `PIPER_URL`.

Those five keys are editable in the admin panel and the database wins over `.env`, so the script
asks the database first (the `flai-postgres` container and its `settings` table):

- a key with no stored override is written to `.env` exactly as before;
- a key saved in the panel is left untouched and the script warns that the panel owns it — change it
  in **Admin Panel → Settings**, not in `.env`.

On a fresh installation the database does not exist yet, so the flags behave as they always did.
Bootstrap keys (`SECRET_KEY`, `DATABASE_URL`, `UPLOAD_FOLDER`, `DOCUMENTS_FOLDER`,
`BRANDING_FOLDER`, `HTTPS_ENABLED`, `FLAI_PLATFORM`, `DEBUG_JS`, `DEBUG_TRANSLATIONS`) are never
stored in the database, and `REDIS_URL` is supplied by Docker Compose.

To hand a key back to `.env`, press ↺ next to its field.

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
