# Конфигурация

## Переменные окружения (.env)

**Обязательные:**
```bash
SECRET_KEY=your_secret_key_here      # Flask session secret
TIMEZONE=Europe/Moscow              # Your timezone
DATABASE_URL=postgresql://flai:flai_password@postgres:5432/flai  # PostgreSQL connection
```

**Режим бэкенда:**
```bash
LLAMACP_BACKEND=llama-swap    # 'llama-swap' (default, recommended) or 'llamacpp' (direct)
LLAMA_SWAP_URL=http://flai-llamaswap:8080  # llama-swap endpoint
VRAM_WAIT_TIMEOUT_S=120                     # Seconds a busy GPU is waited out (ensure_vram_for / queue / video gate) before it is reported unavailable
```

**URL сервисов:**
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

**Параметры изображений и видео по умолчанию:**
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
LTX_VIDEO_TIMEOUT=10800         # Максимальное время генерации видео (сек)
OCR_TIME_BUDGET_S=1800          # Scanned-PDF OCR budget per document task (0 = unlimited)
```

**Настройки повторных попыток подключения к сервисам:**
```bash
SERVICE_RETRY_ATTEMPTS=5
SERVICE_RETRY_DELAY=2
```

**Безопасность сессий:**
```bash
# Set to true ONLY when deployed behind reverse proxy (nginx) with HTTPS enabled
HTTPS_ENABLED=false
# Session lifetime is fixed at 8 hours in code (app/config.py) — no env override.
```

**Глубокий анализ (RLM):**
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

**Веб-поиск и обход сайтов:**
```bash
SEARXNG_URL=http://flai-searxng:8080
SEARXNG_TIMEOUT=10
SEARXNG_MAX_RESULTS=7
SEARXNG_MAX_RESULTS_CHARS=5000
RAG_MAX_RESULTS_CHARS=5000
RAG_CHUNK_SIZE=500             # Символов на фрагмент
RAG_CHUNK_OVERLAP=50           # Перекрытие соседних фрагментов
RAG_CHUNK_STRATEGY=fixed       # fixed | recursive | structured
RAG_TOP_K=80                   # Сколько фрагментов извлекается на запрос
RAG_CONTEXT_PERCENT=30         # Доля контекстного окна под найденные фрагменты
RAG_HYBRID_ENABLED=true        # Сливать плотный и полнотекстовый поиск через RRF (по умолчанию включено)
RAG_HYBRID_CANDIDATES=30       # Кандидатов от каждого поисковика до слияния
RAG_RRF_K=60                   # Константа RRF
RAG_RERANK_ENABLED=false       # Реранкинг верхних кандидатов кросс-энкодером (CPU, без VRAM)
RAG_RERANK_CANDIDATES=10       # Сколько верхних кандидатов переупорядочивает реранкер
RAG_RERANK_TIMEOUT=10          # Секунды до отказа от реранкинга с сохранением исходного порядка
TAVILY_ENABLED=true               # Необязательные личные ключи Tavily; без ключа используется локальный SearXNG
TAVILY_API_URL=https://api.tavily.com
TAVILY_TIMEOUT=20
TAVILY_USAGE_TIMEOUT=8
TAVILY_MAX_RESULTS=7
TAVILY_SEARCH_DEPTH=basic
TAVILY_ADMIN_CACHE_TTL=300     # Сколько секунд переиспользуется успешный ответ о квоте (вкладка «Пользователи» и попап профиля)
TAVILY_NEGATIVE_CACHE_TTL=60   # Сколько секунд помнится недоступность эндпоинта квоты перед повторной попыткой
CRAWL_ENABLED=false               # Sidecar Crawl4AI; включается флагом --with-crawler или этим параметром
CRAWL4AI_API_TOKEN=flai-crawler-token  # Токен compose; веб-приложение получает его как CRAWL_API_TOKEN
CRAWLER_URL=http://flai-crawler:11235
CRAWL_MAX_PAGES=50
CRAWL_MAX_DEPTH=3
CRAWL_TIMEOUT_S=300
CRAWL_PAGE_TIMEOUT_S=30
CRAWL_MAX_PAGE_CHARS=50000
CRAWL_MAX_TOTAL_CHARS=1000000
CRAWL_CONCURRENCY=2
```

**Фоновые задачи:**
```bash
TASKS_ENABLED=1                 # Executor kill switch (0 stops picking up new tasks; running steps finish or abort on cancel flags)
TASK_QUIET_PERIOD_S=15          # Sliding quiet window: a task step is preempted when any chat request arrives after this many quiet seconds
TASK_TOTAL_BUDGET_S=86400       # Pure execution budget per task (seconds); on expiry the task finishes with a final summary
TASK_STEP_MAX_ATTEMPTS=3        # Попыток на шаг с учётом корректирующих проверок, затем шаг помечается failed
TASK_MAX_REVISIONS=8            # Принятых перезаписей плана на задачу; затем план окончателен
TASK_MAX_STEPS=40               # Жёсткий предел длины плана
TASK_REPLAN_EVERY_N=3           # Обычных успешных медиашагов между проверками плана (провалы/ответы проверяются всегда)
TASK_STEP_MAX_REPAIRS=3         # Редактирований после попыток генерации (локальные дефекты); затем остаётся лучший кандидат
TASK_CONTINUITY_ENABLED=1       # Лист визуальной непрерывности перед первым из 2+ генерируемых изображений (0 — выкл.)
TASK_CONTINUITY_MAX_CHARS=2000  # Бюджет листа в символах (стиль + облик объектов)
TASK_STEP_REDO_MAX=1            # Plan-review re-opens of a failed step (a verbatim repeated goal); 0 disables
TASK_POLL_INTERVAL_S=1          # Executor gate/cancel-flag polling interval
TASK_SUBTASK_POLL_CYCLES=10     # Cycles a task holds at one subtask before the liveness check
TASK_SUBTASK_ALIVE_UNKNOWN_MAX=3 # Cycles a failed liveness probe may hold a step before it is failed anyway
TASK_VRAM_WAIT_S=600            # How long one direct step waits for a busy GPU before it gives up
```

**Роутер:**
```bash
ROUTER_CONTEXT_MESSAGES=6    # Recent chat messages fed to the router for context-aware classification
ROUTER_CONTEXT_MSG_CHARS=240 # Max chars of one message in the router's session-context digest
ROUTER_SLM_FACTS=2           # Long-term-memory facts added to the router context
```

**Очередь Redis:**
```bash
REDIS_RESULT_TTL=3600
QUEUE_MAX_WAIT_TIME=300
```

**Отладка:**
```bash
DEBUG_API_ENABLED=false   # Set to 'true' only for development/testing
```

**Публичный API (/v1):**
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

## Переопределение из админ-панели

Почти любую переменную из списка выше можно задать в **Админ-панель → Настройки** — тогда она хранится
в базе. Приоритет: **база > `.env` > значение по умолчанию**. Значение, сохранённое в панели, важнее
переменной окружения; кнопка ↺ рядом с полем удаляет переопределение и возвращает значение из `.env`.

- Сохранённое значение применяется сразу, без перезапуска.
- Параметры сгруппированы (Поиск и обход, Документы и RAG, Задачи, Модели и контекст, Генерация медиа,
  Глубокий анализ, Голос, Память и история, API, Камеры, Служебное, Отладка), поиск фильтрует по
  названию и env-ключу.
- Секреты (`QDRANT_API_KEY`, `HUGGINGFACE_TOKEN`, `CRAWL_API_TOKEN`) никогда не возвращаются в браузер:
  показывается только маска, а пустое поле означает «не менять».
- Каждое изменение пишется в `settings_audit` (кто, когда, было → стало; секреты скрыты) и чистится
  через 45 дней. Обе таблицы входят в полный бэкап.
- `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, пути к папкам, `HTTPS_ENABLED`, `FLAI_PLATFORM` и
  отладочные `DEBUG_JS` / `DEBUG_TRANSLATIONS` остаются только в `.env`: они нужны до подключения к базе
  или определяют поведение процесса.

## Скрипт развёртывания и сохранённые переопределения

`deploy.sh` (и его русский близнец `deploy-ru.sh`) по-прежнему правит `.env`: флаги
`--with-search`, `--with-crawler`, `--with-slm` и выбор голосового движка комментируют или
раскомментируют `SEARXNG_URL`, `CRAWL_ENABLED`, `SLM_URL`, `KOKORO_URL` и `PIPER_URL`.

Эти пять ключей редактируются в админ-панели, а база важнее `.env`, поэтому скрипт сначала
спрашивает базу (контейнер `flai-postgres`, таблица `settings`):

- ключ без сохранённого переопределения записывается в `.env` как раньше;
- ключ, сохранённый в панели, не трогается, а скрипт предупреждает, что им владеет панель —
  менять его нужно в **Админ-панель → Настройки**, а не в `.env`.

На свежей установке базы ещё нет, поэтому флаги работают ровно как прежде.
Bootstrap-ключи (`SECRET_KEY`, `DATABASE_URL`, `UPLOAD_FOLDER`, `DOCUMENTS_FOLDER`,
`BRANDING_FOLDER`, `HTTPS_ENABLED`, `FLAI_PLATFORM`, `DEBUG_JS`, `DEBUG_TRANSLATIONS`) в базе не
хранятся, а `REDIS_URL` передаёт Docker Compose.

Чтобы вернуть ключ в `.env`, нажмите ↺ рядом с его полем.

## Доступ по домену и HTTPS (reverse proxy)

По умолчанию веб-интерфейс доступен по адресу `http://<server-ip>:5000` — веб-сервис публикует порт `5000` (`"5000:5000"` в `docker-compose.gpu.yml` / `docker-compose.cpu.yml`), и Gunicorn слушает `0.0.0.0:5000`.

Чтобы разместить ПЛИИ под собственным доменом, поставьте перед ним reverse proxy (nginx, Caddy, Traefik). Приложение доверяет прокси-заголовкам (`ProxyFix`: `X-Forwarded-Proto`, `X-Forwarded-Host`, `X-Forwarded-For`), поэтому редиректы и `url_for` автоматически подхватят ваш домен и схему HTTPS.

**Шаг 1.** (опционально) Закройте прямой доступ к порту 5000: в `docker-compose.gpu.yml` / `docker-compose.cpu.yml` замените `"5000:5000"` на `"127.0.0.1:5000:5000"` и перезапустите командой `docker compose -f docker-compose.gpu.yml up -d flai-web`.

**Шаг 2.** В `.env`:
```bash
# Set to 'true' ONLY behind an HTTPS reverse proxy (nginx) — enables the Secure flag for session cookies
HTTPS_ENABLED=true
```

**Шаг 3.** Пример конфигурации nginx (`/etc/nginx/sites-available/flai`):
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

**Шаг 4.** Либо то же самое на **Caddy** (`Caddyfile`) — сертификаты выпускаются автоматически:
```
flai.example.com {
    reverse_proxy 127.0.0.1:5000
}
```

Теперь вы можете открыть `https://flai.example.com`.

## Конфигурация Docker

**Настройки Gunicorn (gunicorn_config.py):**

Конфигурация загружается из `gunicorn_config.py`, а не из аргументов командной строки.

| Параметр | Значение | Причина |
|---------|-------|--------|
| workers | 1 | Единственный воркер gunicorn — устраняет гонку за `_gpu_lock` (`threading.Lock` действует только внутри процесса) |
| worker_class | gevent | Асинхронный воркер, оптимизированный для I/O и параллельных соединений |
| timeout | 900s | Запас для длительных операций (редактирование изображений до 15 минут) |
| graceful_timeout | 30s | Плавное завершение воркеров |
| keepalive | 5s | Переиспользование соединений для проверок здоровья |

## Профили Docker Compose

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
