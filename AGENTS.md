# AGENTS.md - FLAI v8.0 Developer Guide

## 1. Project Overview

FLAI (Fully Local AI) is a self-hosted personal AI assistant that runs entirely on-premises with no cloud dependencies. The project provides a modular Flask-based web interface orchestrating multiple AI services built on the llama.cpp ecosystem.

**Version:** 8.0
**License:** MIT
**Python:** 3.9+
**Docker:** Required for deployment

---

## 2. Technology Stack

### Core Components

| Component | Purpose | Technology | Default Port |
|-----------|---------|------------|---------------|
| Flask Web | Web interface, routing, API | Python + Flask | 5000 |
| llama.cpp | LLM inference (chat, reasoning, multimodal, embedding) | C++ + CUDA | 8033 |
| stable-diffusion.cpp | Image generation and editing | C++ + CUDA | 7861 |
| Whisper ASR | Speech-to-text transcription | faster_whisper | 9000 |
| Piper TTS | Text-to-speech synthesis | ONNX + Piper | 18888 |
| Qdrant | Vector database for RAG | Rust | 6333 |
| Redis | Request queue management | C | 6379 |
| PostgreSQL | User accounts, sessions, messages | SQL | 5432 |

### Python Dependencies

```
flask==2.3.3
flask-wtf==1.2.1
flask-limiter==3.5.0
python-dotenv==1.0.0
requests==2.31.0
gunicorn==21.2.0
Pillow==10.1.0
pytz==2023.3
redis==5.0.1
Flask-Babel==2.0.0
qdrant-client==1.9.1
PyPDF2==3.0.1
python-docx==1.1.0
python-magic==0.4.27
psycopg2-binary==2.9.9
```

---

## 3. Models Used

### 3.1 LLM & Embedding Models

| Model | Purpose | Size | License | Download |
|-------|---------|------|---------|----------|
| **Qwen3-4B-Instruct-2507-Q4_K_M** | Fast chat responses | 2.4 GB | Apache 2.0 | HuggingFace |
| **gpt-oss-20b-mxfp4** | Complex reasoning, code generation | 12 GB | Apache 2.0 | HuggingFace |
| **Qwen3VL-8B-Instruct-Q4_K_M** | Multimodal (image analysis) | 4.7 GB + 1.1 GB mmproj | Apache 2.0 | HuggingFace |
| **bge-m3-Q8_0** | Text embedding for RAG | 606 MB | MIT | HuggingFace |
| **bge-reranker-v2-m3-Q4_K_M** | RAG result reranking | 419 MB | MIT | HuggingFace |

### 3.2 Image Generation Models

| Model | Purpose | Size | License |
|-------|---------|------|---------|
| **Z-image-turbo-8b** | Text-to-image generation | ~8 GB |flux-2-klein-4b License |
| **Flux.2 Klein 4B** | Image editing (inpainting, style transfer) | ~4 GB |flux-2-klein-4b License |

### 3.3 TTS Models (Piper)

| Voice | Language | Gender |
|-------|----------|--------|
| ru_RU | Russian | Male |
| ru_RU | Russian | Female |

### 3.4 Download Commands

```bash
# Create models directory
mkdir -p services/llamacpp/models
mkdir -p services/sd_cpp/models/diffusion_models
mkdir -p services/sd_cpp/models/vae
mkdir -p services/sd_cpp/models/text_encoders
mkdir -p services/piper/piper_models

# Download LLM models (llama.cpp GGUF format)
# Qwen3-4B-Instruct-2507-Q4_K_M (~2.4 GB)
wget -O services/llamacpp/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  "https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

# gpt-oss-20b-mxfp4 (~12 GB)
wget -O services/llamacpp/models/gpt-oss-20b-mxfp4.gguf \
  "https://huggingface.co/gpt-oss-20b-mxfp4/resolve/main/gpt-oss-20b-mxfp4.gguf"

# Qwen3VL-8B-Instruct-Q4_K_M (~4.7 GB) - requires subdirectory
mkdir -p services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/Qwen3VL-8B-Instruct-Q4_K_M.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf"

# mmproj for multimodal (~1.1 GB)
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/mmproj-F16.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-model-f16.gguf"

# bge-m3-Q8_0 embedding model (~606 MB)
wget -O services/llamacpp/models/bge-m3-Q8_0.gguf \
  "https://huggingface.co/BAAI/bge-m3-gguf/resolve/main/bge-m3-Q8_0.gguf"

# bge-reranker-v2-m3-Q4_K_M (~419 MB)
wget -O services/llamacpp/models/bge-reranker-v2-m3-Q4_K_M.gguf \
  "https://huggingface.co/BAAI/bge-reranker-v2-m3-GGUF/resolve/main/bge-reranker-v2-m3-Q4_K_M.gguf"

# Download Piper voices (see services/piper/download-voices.sh)
```

---

## 4. Architecture

### Single-Server Architecture

All services run on one machine with GPU sharing:

```
┌──────────────────────────────────────────────────────┐
│                 FLAI Web (Flask)                     │
│   Redis Queue → Model Router → Response              │
└──────┬──────────┬────────────┬───────────────────────┘
       │          │            │
       ▼          ▼            ▼
┌──────────┐ ┌──────────┐ ┌──────────────┐
│llama.cpp │ │  sd.cpp  │ │  Whisper     │
│(router) │ │          │ │  (ASR)       │
└──────────┘ └──────────┘ └──────────────┘
       │                              │
       ▼                              ▼
┌──────────────────┐        ┌──────────────────┐
│  Qdrant (RAG)    │        │  Piper (TTS)    │
└──────────────────┘        └──────────────────┘
```

### Model Router

llama.cpp runs in router mode with:
- `--models-dir /models/` - Directory containing GGUF models
- `--models-preset /models/models-preset.ini` - Model configurations
- `--models-max 1` - Only one model in VRAM at a time (prevents OOM)

Each model type has its own service URL configuration stored in PostgreSQL `model_configs` table.

---

## 5. Deployment

### 5.1 Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| GPU | 16 GB VRAM | 24+ GB VRAM |
| RAM | 32 GB | 64 GB |
| Storage | 100 GB | 500 GB (for models) |
| CPU | 6 cores | 12+ cores |

### 5.2 Environment Setup

1. **Create .env file:**

```bash
cp .env.example .env
# Edit .env with your settings
```

Required environment variables:
- `SECRET_KEY` - Flask session secret
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string
- `LLAMACPP_URL` - llama.cpp server URL
- `QDRANT_URL` - Qdrant server URL (optional for RAG)
- `PIPER_URL` - Piper TTS server URL (optional)
- `SD_WRAPPER_URL` - stable-diffusion.cpp wrapper URL (optional)

### 5.3 Deploy Scripts

#### deploy.sh (English version)

```bash
#!/bin/bash
# FLAI Deployment Script (English)
# Usage: ./deploy.sh

set -e

echo "=== FLAI Deployment Started ==="

# 1. Create required directories
echo "Creating directories..."
mkdir -p data/uploads data/documents
mkdir -p services/llamacpp/models
mkdir -p services/sd_cpp/models/diffusion_models
mkdir -p services/sd_cpp/models/vae
mkdir -p services/sd_cpp/models/text_encoders
mkdir -p services/piper/piper_models

# 2. Download models (if not exists)
echo "Checking models..."
# (Model download commands)

# 3. Build Docker images
echo "Building Docker images..."
docker compose build

# 4. Start services
echo "Starting services..."
docker compose up -d

# 5. Wait for services
echo "Waiting for services..."
sleep 30

# 6. Check health
curl -f http://localhost:5000/health || echo "Health check failed"

echo "=== Deployment Complete ==="
```

#### deploy-ru.sh (Russian version)

```bash
#!/bin/bash
# FLAI Скрипт развёртывания (Русский)
# Использование: ./deploy-ru.sh

set -e

echo "=== Начало развёртывания FLAI ==="

# 1. Создание директорий
echo "Создание директорий..."
mkdir -p data/uploads data/documents
mkdir -p services/llamacpp/models
mkdir -p services/sd_cpp/models/diffusion_models
mkdir -p services/sd_cpp/models/vae
mkdir -p services/sd_cpp/models/text_encoders
mkdir -p services/piper/piper_models

# 2. Загрузка моделей
echo "Проверка моделей..."
# (Команды загрузки моделей)

# 3. Сборка Docker образов
echo "Сборка Docker образов..."
docker compose -f docker-compose.all.yml build

# 4. Запуск сервисов
echo "Запуск сервисов..."
docker compose -f docker-compose.all.yml up -d

# 5. Ожидание готовности
echo "Ожидание сервисов..."
sleep 30

# 6. Проверка здоровья
curl -f http://localhost:5000/health || echo "Проверка здоровья не пройдена"

echo "=== Развёртывание завершено ==="
```

### 5.4 Docker Compose Services

```yaml
services:
  web:        # Flask application
  llamacpp:   # llama.cpp router server
  sd_cpp:     # stable-diffusion.cpp
  whisper:    # Whisper ASR
  piper:      # Piper TTS
  qdrant:     # Vector database (optional)
  redis:      # Queue management
  postgres:   # Database
```

---

## 6. Initial Setup

### 6.1 First Run

After starting the container, the default admin user is created automatically:

- **Login:** `admin`
- **Password:** Randomly generated (see container logs or .env)

To set a specific admin password:

```bash
# Via Flask CLI
docker exec -it flai-web flask cli set-admin-password new_password
```

### 6.2 Model Configuration

1. Open admin panel: http://localhost:5000/admin
2. Navigate to **Model Configuration**
3. Select models for each type:
   - Chat: Qwen3-4B-Instruct-2507-Q4_K_M
   - Reasoning: gpt-oss-20b-mxfp4
   - Multimodal: Qwen3VL-8B-Instruct-Q4_K_M
   - Embedding: bge-m3-Q8_0
   - Reranker: bge-reranker-v2-m3-Q4_K_M
4. Save configuration

### 6.3 Verify Services

Check health status:
```bash
curl http://localhost:5000/health
```

Expected response:
```json
{
  "status": "ok",
  "services": {
    "web": "ok",
    "database": "ok",
    "redis": "ok",
    "llamacpp": "ok"
  }
}
```

---

## 7. Testing

### 7.1 Unit Tests

Run all unit tests:
```bash
pytest tests/ -v
```

Run specific test:
```bash
pytest tests/test_routes.py -v
pytest tests/test_rag_module.py -v
```

Coverage report:
```bash
pytest tests/ --cov=app --cov=modules --cov-report=html
```

### 7.2 Load Tests

Using Locust:
```bash
# Standard load test
locust -f tests/load/locustfile.py --host=http://localhost:5000

# Public endpoints only (no auth required)
locust -f tests/load/locustfile_public.py --host=http://localhost:5000
```

Web UI for load testing: http://localhost:8089

### 7.3 Test Structure

```
tests/
├── conftest.py           # Fixtures and mocks
├── test_routes.py        # API route tests
├── test_rag_module.py   # RAG functionality tests
├── test_sd_cpp_module.py # Image generation tests
├── test_audio_module.py  # Voice transcription tests
├── test_tts_module.py    # TTS tests
├── test_security.py      # Security tests
├── test_integration.py  # Integration tests
└── load/
    ├── locustfile.py     # Load tests (requires auth)
    ├── locustfile_public.py  # Public endpoint tests
    ├── run_load_test.sh # Load test runner
    └── README.md        # Load test documentation
```

---

## 8. Code Conventions

### 8.1 Comments

All code comments must be in **English**. This includes:
- Function docstrings
- Inline comments
- TODO notes
- Module descriptions

```python
# Good
def process_request(data):
    """Process incoming request and return result."""
    # Validate input data
    if not data:
        return None
    return data

# Bad (Russian comment)
def process_request(data):
    """Обработать запрос и вернуть результат."""
    # Проверить входные данные
```

### 8.2 Logging

All log messages must be in **English**.

```python
import logging

logger = logging.getLogger(__name__)

# Good
logger.info("Processing request from user %s", user_id)
logger.warning("Model not available, using fallback")

# Bad
logger.info("Обработка запроса от пользователя %s", user_id)
```

### 8.3 User Messages

User-facing messages (errors, notifications, UI text) must use **Babel translations** to support multiple languages:

```python
from flask_babel import gettext as _

# Good - supports ru/en
error_msg = _('Invalid login or password')
success_msg = _('Image generated successfully')

# Bad - hardcoded language
error_msg = "Неверный логин или пароль"
```

Translation files:
- `translations/ru/LC_MESSAGES/messages.po` - Russian
- `translations/en/LC_MESSAGES/messages.po` - English

### 8.4 File Organization

```
project/
├── app/                    # Main Flask application
│   ├── __init__.py         # App factory
│   ├── config.py           # Configuration
│   ├── database.py         # PostgreSQL abstraction
│   ├── llamacpp_client.py  # llama.cpp API client
│   ├── routes/             # Flask blueprints
│   ├── static/css/         # CSS files
│   └── templates/           # Jinja2 templates
├── modules/                # AI modules
│   ├── base.py            # Chat module
│   ├── multimodal.py      # Image analysis
│   ├── rag.py             # RAG functionality
│   ├── sd_cpp.py          # Image generation
│   ├── audio.py           # Voice transcription
│   └── tts.py             # Text-to-speech
├── services/              # External services
│   ├── llamacpp/          # llama.cpp models
│   ├── sd_cpp/            # Stable diffusion models
│   ├── piper/             # TTS voices
│   └── ...
├── tests/                 # Test suite
├── translations/          # Babel translations
├── deploy.sh              # English deployment
├── deploy-ru.sh           # Russian deployment
├── docker-compose.all.yml # Full stack compose
└── README.md              # Project documentation
```

---

## 9. Achievements (v8.0)

### 9.1 llama.cpp Migration

- **Old:** Ollama with separate model management
- **New:** llama.cpp router mode with `--models-dir` and `--models-preset`
- **Benefits:** Single server, dynamic model switching, OpenAI-compatible API

### 9.2 stable-diffusion.cpp Migration

- **Old:** Automatic1111 web UI
- **New:** stable-diffusion.cpp with sd-wrapper HTTP API
- **Models:**
  - Z-image-turbo 8B for image generation
  - Flux.2 Klein 4B for image editing

### 9.3 Piper TTS Optimization

- Chunked audio playback for large texts
- Background processing
- Memory-efficient streaming

### 9.4 Backup/Restore

- Full database backup (PostgreSQL)
- File storage backup (uploads, documents)
- Automatic and manual backup options

### 9.5 Context Window Optimization

- Dynamic token estimation
- Smart history truncation
- Configurable safety margins

### 9.6 RAG Reranking

- Cross-encoder reranker via llama.cpp `/v1/rerank`
- Relevance threshold configuration
- Improved search accuracy

---

## 10. Maintenance

### 10.1 Backup

```bash
# Create backup
docker exec flai-web flask cli backup create

# List backups
docker exec flai-web flask cli backup list

# Restore from backup
docker exec flai-web flask cli backup restore <backup_id>
```

### 10.2 Logs

```bash
# Application logs
docker logs flai-web

# llama.cpp logs
docker logs flai-llamacpp

# All services
docker compose logs -f
```

### 10.3 Updates

```bash
# Pull latest changes
git pull

# Rebuild and restart
docker compose build
docker compose up -d
```

---

## 11. Troubleshooting

### Service unavailable

```bash
# Check health
curl http://localhost:5000/health

# Check specific service
docker exec flai-web curl -s http://flai-llamacpp:8033/v1/models
```

### Model not loading

```bash
# Check llama.cpp logs
docker logs flai-llamacpp | grep -i error

# Verify model files exist
docker exec flai-llamacpp ls -la /models/
```

### Database connection issues

```bash
# Check PostgreSQL
docker exec flai-postgres psql -U flai -c "SELECT 1"

# Check connection from web
docker exec flai-web python -c "from app.database import get_db; print('OK')"
```

### Circuit Breaker

The project uses a Circuit Breaker pattern to prevent cascading failures when llama.cpp is unavailable.

**Configuration** (in `app/llamacpp_client.py`):
```python
self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
```

**States**:
- **CLOSED**: Normal operation, requests pass through
- **OPEN**: Service is failing, requests are blocked (fail fast for 60s)
- **HALF_OPEN**: Testing if service recovered, allows one test request

**Behavior**:
- After 3 consecutive failures, circuit opens
- While open, all requests fail immediately (no timeout waiting)
- After 60s, allows one test request to check recovery
- If successful, closes circuit; if fails, opens again

---

## 12. Security Considerations

- All user data stored locally (no cloud)
- Session-based authentication with secure cookies
- CSRF protection enabled
- Rate limiting on login (5 attempts/minute)
- File access control (users can only access own files)
- HMAC-signed Redis queue tasks
- Audit logging for login attempts and admin actions

---

## 13. Contributing

1. Fork the repository
2. Create a feature branch
3. Follow code conventions (English comments, English logs)
4. Add tests for new functionality
5. Submit pull request

---

## 14. Documentation Requirements

### 14.1 README Files

Both README.md and README-ru.md must be kept synchronized and reflect the current project state:

- **README.md** (English) — primary documentation
- **README-ru.md** (Russian) — translated version

Key sections that must be synchronized:
- Architecture section with "What's New in v8.0" table
- System components table (PostgreSQL, not SQLite)
- Model information with licenses and sizes
- Deployment instructions

### 14.2 Model Documentation

Each model used in the project must be documented with:
- Model name and purpose
- Approximate size
- License
- Source URL (HuggingFace)

Required models for documentation:
| Model | Purpose | Size | License |
|-------|---------|------|---------|
| Qwen3-4B-Instruct-2507-Q4_K_M | Chat | 2.4 GB | Apache 2.0 |
| gpt-oss-20b-mxfp4 | Reasoning | 12 GB | Apache 2.0 |
| Qwen3VL-8B-Instruct-Q4_K_M | Multimodal | 4.7 GB + 1.1 GB mmproj | Apache 2.0 |
| bge-m3-Q8_0 | Embedding | 606 MB | MIT |
| bge-reranker-v2-m3-Q4_K_M | Reranking | 419 MB | MIT |

---

*Last updated: 2026-04-16*