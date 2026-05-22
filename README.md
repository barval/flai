<div align="center">
  <img src="docs/logo.png" alt="Fully Local AI (FLAI)" width="200">

  # Fully Local AI (FLAI)

  **FLAI — a multifunctional, fully local, privacy-first AI platform.**

  [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
  [![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
  [![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?logo=docker&logoColor=white)](https://www.docker.com/)

[English](README.md) | [Русский](README-ru.md)
</div>

---

## ✨ Features

### 🤖 Core AI Capabilities
- 💬 **Intelligent Chat** – smart request routing (fast models for simple queries, powerful models for complex reasoning)
- 🧠 **Advanced Reasoning** – dedicated model for calculations, code generation, creative writing
- 🔍 **Multimodal Analysis** – upload images and ask questions about their content (llama.cpp + mmproj)
- 🎨 **Image Generation** – create images from text using stable-diffusion.cpp with automatic prompt optimization
- ✏️ **Image Editing** – upload an image and ask to edit it (Flux.2 Klein 4B model: change colors, remove objects, stylize)
- 🎤 **Voice Transcription** – convert voice messages to text using Whisper ASR (faster_whisper)
- 🗣️ **Text-to-Speech** – hear responses spoken aloud via Piper TTS (male and female voices in English and Russian)

### 📁 Document & Knowledge Management
- 📚 **RAG with Qdrant** – upload documents (PDF, DOC, DOCX, TXT) and ask questions about their content
- 🗂️ **Chat Sessions** – multiple independent conversations with auto-titling
- 💾 **Export Chats** – save conversations as HTML files with embedded media

### 🏠 Home Integration (Optional)
- 📹 **Camera Surveillance** – request snapshots from IP cameras and analyze them with multimodal models
- 🔐 **Access Control** – granular camera permissions per user via admin panel

### 🔒 Privacy & Security
- 🏠 **100% Local** – all processing happens on your hardware; no data leaves your network
- 🔐 **Session-based Auth** – secure user authentication with password hashing (Werkzeug)
- 🛡️ **File Access Control** – uploaded files are served only to authorized users
- 🧹 **Data Isolation** – each user's sessions, messages, and documents are strictly separated
- 🔑 **CSRF Protection** – Cross-Site Request Forgery protection for all forms
- 🚦 **Rate Limiting** – brute-force attack protection on login (5 attempts/minute)
- 🔒 **Session Security** – HttpOnly and SameSite cookies, secure flag for HTTPS
- 📝 **Audit Logging** – login attempts and admin actions are logged
- 🔐 **HMAC-signed Queue** – Redis queue tasks are signed to prevent tampering
- 🛡️ **Input Validation** - Strict validation of user inputs (logins, passwords, model parameters) to prevent injection attacks and malformed data.

### 👥 User Experience
- 🌐 **Multi-language Support** – full interface and AI responses in Russian and English
- 🌓 **Dark/Light Theme** – toggle between themes with persistent preference storage
- 🎚️ **Voice Gender Selection** – choose male or female voice for TTS responses
- 🎭 **Response Styles** – choose the AI's conversational tone in real-time from the chat header: neutral, academic, professional, friendly, or funny. Affects all responses including text, RAG, image analysis, and camera queries.
- 📊 **Request Queue** – real-time status tracking with position indicators for queued requests
- 📎 **File Attachments** – support for images, audio files, and documents in conversations
- 🔔 **Notifications** – unread message indicators and blinking status icons for processing/queued requests

### ⚙️ Administration
- 👤 **User Management** – add, edit, delete users; change passwords; assign service classes
- 🔑 **Camera Permissions** – control which users can access which cameras (Optional)
- 🤖 **Model Management** – select and configure GGUF models for chat, reasoning, multimodal, and embedding directly from the admin panel
- 💾 **Backup & Restore** – create and restore full or user-only backups directly from the admin interface
- 📈 **System Monitoring** – view database sizes and system statistics
- 🔧 **CLI Tools** – manage admin password via Flask CLI command

---

## 🏗️ Architecture

FLAI is a modular Flask application that orchestrates self-hosted AI services built on the llama.cpp ecosystem.

### What's New in v8.5

| v8.5 (New) | Notes |
|------------|-------|
| 🔄 **Page-refresh recovery** | ⚡ indicator, live streaming, and final response all survive F5 during generation — `onStreamToken`/`onResultCompleted` now handle missing `pendingRequestIds` after reload |
| 🖥️ **VRAM monitor & auto-degradation** | Background VRAM polling via `nvidia-smi` every 60s. Progressive model degradation (100%→0% n_gpu_layers in 4 steps) on OOM. `_MAX_SAFE_NGL` per-VRAM-tier safety caps (16GB → ngl 24). All models in single `llm_fast` swap group |
| 📊 **VRAM calculator** | New `/admin/api/model-estimate` endpoint estimates VRAM per model (weights + KV cache + compute). Model config UI with auto-calculated `n_gpu_layers` slider |
| 🎨 **SD offload system** | Refactored `sd_wrapper.py`: 4-level VRAM offload (0=full GPU → 3=full CPU), progressive on OOM. VRAM headroom check (500MB) before SD generation |
| ⚡ **Live token/s display** | Real-time tokens-per-second estimate during streaming + final token/s in message header. `completion_tokens` stored in DB and passed through SSE |
| 🔧 **Model config cache fix** | TTL cache replaced with `updated_at`-based versioning — eliminates cross-worker inconsistency with gunicorn `workers=2` |
| 🐛 **Architecture display fix** | Numpy byte-string decoding (`[113 119 101 110 51]` → `qwen3`) in admin panel. Regex handles leading-space variants |
| 🗃️ **GGUF metadata expansion** | `parameter_count`, `head_count`, `head_count_kv`, `key_length`, `value_length` scanned and stored in DB |


### Core Components

| Component | Purpose | Technology | Default Port |
|-----------|---------|------------|--------------|
| **Flask Web** | Web interface, routing, API | Python | 5000 |
| **llama-swap** | Dynamic LLM model routing & management (llama.cpp proxy) | Go + llama.cpp | 8080 |
| **stable-diffusion.cpp** | Image generation (Z_image_turbo) and editing (Flux.2 Klein 4B) | C++ + CUDA | 7861 |
| **Whisper ASR** | Speech-to-text transcription | faster_whisper | 9000 |
| **Piper TTS** | Text-to-speech synthesis | ONNX + Piper | 8888 |
| **Qdrant** | Vector database for RAG | Rust | 6333 |
| **Redis** | Request queue management | C | 6379 |
| **PostgreSQL** | User accounts, sessions, messages | SQL | 5432 |
| **Resource Manager** | Adaptive GPU/CPU/RAM management, prevents OOM errors, coordinates GPU access | Python |
| **Circuit Breaker** | Prevents cascading failures by blocking calls to failing services (llama.cpp, sd.cpp, Whisper) after repeated errors | Python |

### Single-Server Architecture

All services run on one machine with GPU sharing:

```text
┌──────────────────────────────────────────────────────┐
│                 FLAI Web (Flask)                     │
│   Redis Queue → Model Router → Response              │
└──────┬──────────┬────────────┬───────────────────────┘
       │          │            │
       ▼          ▼            ▼
   llama-swap  sd.cpp      Whisper/Piper/Qdrant
   :8080       :7861       (separate containers)
   (dynamic model routing via llama-swap)
```

**Dynamic Model Routing**: llama-swap acts as a proxy to llama.cpp, dynamically loading/unloading GGUF models on demand. Only one model occupies VRAM at a time, with automatic switching based on request type. Model configuration is managed via the admin panel and stored in the database.

---

## 📋 System Requirements

### Hardware Recommendations
| Component | Minimum | Recommended | Optimal |
|-----------|---------|-------------|---------|
| **RAM** | 16 GB | 32 GB | 32+ GB |
| **CPU** | 4 cores | 4+ cores | 8+ cores |
| **GPU** | NVIDIA 8-12 GB VRAM | NVIDIA 16 GB VRAM | NVIDIA 24+ GB VRAM |
| **Storage** | 40 GB | 60+ GB SSD | 100+ GB SSD NVMe |

### Software Prerequisites
- Linux server with **NVIDIA GPU** (CUDA support required)
- **NVIDIA drivers** installed on host
- **NVIDIA Container Toolkit** installed
- Docker Engine ≥ 20.10
- Docker Compose ≥ 2.0
- Internet connection (only for initial model downloads)

> 💡 **Note**: After downloading GGUF models, FLAI works completely offline.

---

### 💻 Running without GPU (CPU-only mode)

FLAI can operate on CPU-only servers using automatic detection in the deployment script. When no NVIDIA GPU is found, the script will use CPU-optimized images for llama.cpp and stable-diffusion.cpp. Performance will be significantly slower, but all features remain functional.

- Chat and reasoning: works, but may be 3-10x slower.
- Image generation: works, but generation time can be 10-30 minutes per image.
- Voice processing (Whisper, Piper) and document search (RAG) are unaffected.

To force CPU mode even if a GPU is present, you can manually run:
```bash
docker compose -f docker-compose.cpu.yml --profile with-image-gen --profile with-voice --profile with-rag up -d
```

---

## 🚀 Quick Start

> 💡 **Note**: You must have the **NVIDIA drivers** and **NVIDIA Container Toolkit** installed.

### Option A: Automated Deployment (Recommended)

A single deployment script handles everything: environment setup, model downloads, building, and launching.

```bash
git clone https://github.com/barval/flai.git
cd flai

# Core chat + llama.cpp only
./deploy.sh --download-models

# + Image generation/editing
./deploy.sh --download-models --with-image-gen

# + Voice (Whisper ASR + Piper TTS)
./deploy.sh --download-models --with-image-gen --with-voice

# Everything including RAG (Qdrant)
./deploy.sh --download-models --with-image-gen --with-voice --with-rag

# Run tests after deployment
./deploy.sh --download-models --with-image-gen --run-tests
```

### Option B: Manual Deployment

If you prefer step-by-step control:

### 1. Clone and Configure

```bash
# Clone the repository
git clone https://github.com/barval/flai.git
cd flai

# Create directories and specify the owner
sudo mkdir -p data \
              data/uploads \
              data/documents
sudo chown -R 1000:1000 data

# Copy environment template
cp .env.example .env

# Generate a secure secret key
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")|" .env

# Generate an API key for Qdrant
sed -i "s|^QDRANT_API_KEY=.*|QDRANT_API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")|" .env

# Edit .env with your settings (timezone, API URLs, etc.)
nano .env
```

### 2. Download GGUF Models

#### LLM Models (chat, reasoning, multimodal, embedding)

```bash
mkdir -p services/llamacpp/models

# Chat model (fast responses)
wget -O services/llamacpp/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

# Reasoning model (complex tasks)
wget -O services/llamacpp/models/gpt-oss-20b-Q4_K_M.gguf \
  "https://huggingface.co/unsloth/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b-Q4_K_M.gguf"

# Multimodal model (image analysis) — must be in subdirectory with mmproj
mkdir -p services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/Qwen3VL-8B-Instruct-Q4_K_M.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf"
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/mmproj-F16.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-8B-Instruct-F16.gguf"

# Embedding model (RAG)
wget -O services/llamacpp/models/bge-m3-Q8_0.gguf \
  "https://huggingface.co/gpustack/bge-m3-GGUF/resolve/main/bge-m3-Q8_0.gguf"
```

#### Image Generation Models (Z_image_turbo)

```bash
mkdir -p services/sd_cpp/models/{diffusion_models,vae,text_encoders}

# Diffusion model
wget -O services/sd_cpp/models/diffusion_models/z_image_turbo-Q8_0.gguf \
  "https://huggingface.co/bartowski/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q8_0.gguf"

# VAE
wget -O services/sd_cpp/models/vae/ae.safetensors \
  "https://huggingface.co/bartowski/Z-Image-Turbo-GGUF/resolve/main/ae.safetensors"

# LLM text encoder (shared with chat)
wget -O services/llamacpp/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  "https://huggingface.co/bartowski/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
```

#### Image Editing Models (Flux.2 Klein 4B)

```bash
# Diffusion model for editing
wget -O services/sd_cpp/models/diffusion_models/flux-2-klein-4b-Q8_0.gguf \
  "https://huggingface.co/bartowski/FLUX.2-Klein-dev-GGUF/resolve/main/flux-2-klein-4b-Q8_0.gguf"

# VAE for editing
wget -O services/sd_cpp/models/vae/flux2_ae.safetensors \
  "https://huggingface.co/bartowski/FLUX.2-dev-GGUF/resolve/main/flux2_ae.safetensors"
```

> The text encoder `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` is **shared** between generation and editing. Download it once.

> ⚠️ **Important**: Multimodal models **must** be placed in a subdirectory named after the model, with the `mmproj-*.gguf` file inside. The llama.cpp router automatically discovers and loads the projector.

### 3. Build and Start Services

```bash
# Chat and reasoning only (no image generation)
docker compose -f docker-compose.gpu.yml up -d

# With image generation
docker compose -f docker-compose.gpu.yml --profile with-image-gen up -d

# With voice features
docker compose -f docker-compose.gpu.yml --profile with-voice up -d

# Full stack: chat + images + voice + RAG
docker compose -f docker-compose.gpu.yml --profile with-image-gen --profile with-voice --profile with-rag up -d
```

> ⏱️ **First build takes time**: stable-diffusion.cpp is compiled from source (~5-10 minutes). Subsequent builds use the cache.

### 4. Set Admin Password

```bash
docker exec flai-web flask admin-password YourSecurePassword123
```

### 5. Configure Models in Admin Panel

1. Open `http://localhost:5000` and log in as `admin`
2. Go to **Admin Panel** → **Models** tab
3. For each module (Chat, Reasoning, Multimodal, Embedding):
   - Select the GGUF model from the dropdown
   - Adjust parameters if needed (Context Length, Temperature, Top P, Repeat Penalty, Timeout)
   - Click **Save**
4. For Image Generation: Ensure `SD_WRAPPER_URL=http://flai-sd:7861` is set in `.env`

### 6. You're Ready!

Now you can:
- 💬 **Chat with AI** — smart routing for fast and complex responses
- 🧠 **Advanced Reasoning** — complex calculations, code generation, creative writing
- 🔍 **Analyze Images** — upload photos and ask questions (multimodal)
- 🎨 **Generate Images** — create images from text descriptions
- ✏️ **Edit Images** — upload and edit (change colors, remove objects, stylize)
- 🎤 **Send Voice Messages** — speech-to-text via Whisper ASR
- 🗣️ **Listen to Responses** — text-to-speech via Piper TTS (male/female, EN/RU)
- 📚 **Search Documents** — upload PDF/DOC/TXT and ask questions (RAG)
- 🗂️ **Multiple Chat Sessions** — separate conversations with auto-titling
- 💾 **Export Chats** — save conversations as HTML with embedded media
- 📹 **View Cameras** — IP camera snapshots analyzed by AI
- 💾 **Backup & Restore** — full or user-only backups from the admin panel
- 🔧 **CLI Tools** — admin password reset, orphaned file cleanup

---

## 🔧 Configuration

### Environment Variables (.env)

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
```

**Image Generation Defaults:**
```bash
SD_CPP_DEFAULT_WIDTH=1024
SD_CPP_DEFAULT_HEIGHT=1024
SD_CPP_DEFAULT_CFG_SCALE=1.0    # 1.0 for flow-matching models (Z_image_turbo)
SD_CPP_DEFAULT_STEPS=10         # 10 for Z_image_turbo
SD_CPP_TIMEOUT=900
```

**Service Retry Settings:**
```bash
SERVICE_RETRY_ATTEMPTS=15
SERVICE_RETRY_DELAY=2
```

**Session Security:**
```bash
# Set to true ONLY when deployed behind reverse proxy (nginx) with HTTPS enabled
HTTPS_ENABLED=false
PERMANENT_SESSION_LIFETIME=28800    # 8 hours
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

### Docker Configuration

**Gunicorn Settings (gunicorn_config.py):**

Configuration is loaded from `gunicorn_config.py`, not inline CLI args.

| Setting | Value | Reason |
|---------|-------|--------|
| workers | 1 | Minimal RAM (+40MB); all requests wait for the same AI backend |
| threads | 4 | Handles 4 concurrent I/O-bound connections |
| worker_class | gthread | Optimal for waiting on AI responses |
| timeout | 900s | Accommodates long operations (image editing up to 15 min) |
| graceful_timeout | 30s | Graceful worker shutdown |
| keepalive | 5s | Connection reuse for health checks |

### Docker Compose Profiles

```bash
# Start all services
docker compose -f docker-compose.gpu.yml --profile with-image-gen --profile with-voice --profile with-rag up -d

# Chat + voice only
docker compose -f docker-compose.gpu.yml --profile with-voice up -d

# Chat only (no images, no voice)
docker compose -f docker-compose.gpu.yml up -d

# Stop all services
docker compose -f docker-compose.gpu.yml down --remove-orphans

# View logs
docker compose -f docker-compose.gpu.yml logs -f web
```

---

## 🤖 Model Setup

### GGUF Model Structure

llama.cpp runs in **router mode** (`--models-dir`), dynamically loading models from a shared directory:

```
services/llamacpp/models/
├── Qwen3-4B-Instruct-2507-Q4_K_M.gguf     # Chat
├── gpt-oss-20b-Q4_K_M.gguf                  # Reasoning
├── bge-m3-Q8_0.gguf                        # Embedding
└── Qwen3VL-8B-Instruct-Q4_K_M/             # Multimodal (subdirectory!)
    ├── Qwen3VL-8B-Instruct-Q4_K_M.gguf
    └── mmproj-F16.gguf                     # Vision projector
```

> ⚠️ **Multimodal models require a subdirectory** with the projector file named `mmproj-*.gguf` inside. The model server auto-discovers and loads it.

### Configure Models in Admin Panel

1. Log in as admin and go to `/admin` → **Models** tab
2. For each module (Chat, Reasoning, Multimodal, Embedding):
   - Select the GGUF model from the dropdown, set parameters, click **Save**

> 💡 **Changing the embedding model triggers automatic re-indexing** of all documents.

### Model Parameters

| Parameter | Chat | Reasoning | Multimodal | Embedding |
|-----------|------|-----------|------------|-----------|
| Context Length | 8192 | 32768 | 8192 | 512 |
| Temperature | 0.1 | 0.7 | 0.7 | – |
| Top P | 0.1 | 0.9 | 0.9 | – |
| Repeat Penalty | 1.1 | 1.15 | 1.1 | – |
| Timeout (s) | 60 | 300 | 120 | 30 |

---

## 🎨 Image Generation & Editing

### Generation Model

The project uses **Z_image_turbo** as the only image generation model:

| Model | Steps | CFG Scale | Resolution | Notes |
|-------|-------|-----------|------------|-------|
| **Z_image_turbo** | 10 | 1.0 | 1024×1024 | Fast, flow-matching |

Configure via `SD_MODEL_TYPE` in `.env`:
```bash
SD_MODEL_TYPE=z_image_turbo
```

### Image Editing (Flux.2 Klein 4B)

Upload an image and ask to edit it (e.g., *"change the pupils to green"*, *"remove the second sun"*). The system uses:
1. **Multimodal model** (Qwen3VL) to analyze the image and generate an edit prompt
2. **Flux.2 Klein 4B** model via stable-diffusion.cpp to perform the edit
3. The original image is preserved except for the requested changes

Editing uses separate model files and runs independently from generation — no conflict between the two.

### stable-diffusion.cpp Build

The `sd_cpp` service is **built from source** during first `docker compose up`:
1. Clones `https://github.com/leejet/stable-diffusion.cpp`
2. Initializes git submodules (`ggml`, `thirdparty/*`)
3. Compiles with CUDA 13.0.1 (`cmake -DSD_CUDA=ON`)
4. Produces `sd-server` and `sd-cli` binaries

> ⏱️ **First build**: ~5-10 minutes depending on CPU. Subsequent builds use Docker cache.

### Configuration

```bash
# sd-wrapper HTTP API (port 7861)
SD_WRAPPER_URL=http://flai-sd:7861
SD_CPP_TIMEOUT=900                  # Timeout for gen/edit operations (seconds)
SD_CPP_DEFAULT_WIDTH=1024
SD_CPP_DEFAULT_HEIGHT=1024
SD_CPP_DEFAULT_CFG_SCALE=1.0
SD_CPP_DEFAULT_STEPS=10
```

---

## 🎤 Voice Features Setup

### Whisper ASR

Uses `onerahmet/openai-whisper-asr-webservice` (faster_whisper engine).

```bash
# Enable voice features
docker compose -f docker-compose.gpu.yml --profile with-voice up -d
```

### Piper TTS

Uses ONNX Piper models for text-to-speech.

```bash
# Download voice models (see services/piper/download-voices.sh)
mkdir -p services/piper/piper_models

# English (male)
curl -L -o services/piper/piper_models/en_US-ryan-medium.onnx \
  "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx"

# Russian (male)
curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx \
  "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx"
```

---

## 📚 RAG (Document Search) Setup

### 1. Configure RAG in Admin Panel

After starting the services, log in as admin and go to **Admin Panel → Models** tab. Scroll down to the **Chunks** section. Here you can fine-tune RAG behavior:

- **Chunk Size (characters):** How documents are split into pieces for indexing.
- **Chunk Overlap (characters):** Number of overlapping characters between consecutive chunks.
- **Chunk Strategy:** `fixed` (by character count) or `recursive` (by headings/paragraphs).
- **Number of chunks (top_k):** Maximum number of chunks to retrieve from Qdrant per query.
- **Threshold (documents):** Minimum similarity score for general document queries.
- **Threshold (reasoning):** Minimum similarity score when RAG is triggered from a reasoning request.

Click **Save** to apply changes. If chunking parameters (size or strategy) are modified, a background reindex of all documents is triggered automatically.

> **Note:** Environment variables like `RAG_CHUNK_SIZE` in `.env` are only used as initial defaults before the first configuration save. The primary configuration is stored in the database.

### 2. Enable in Docker Compose
```bash
docker compose -f docker-compose.gpu.yml --profile with-rag up -d
```

### 3. Upload Documents
1. Log in to web interface
2. Click **Documents** tab in sidebar
3. Click ➕ to upload PDF, DOC, DOCX, or TXT files
4. Wait for indexing to complete (status: ✅ Indexed)

---

## 📹 Camera Integration (Optional)

The camera module connects to a separate `room-snapshot-api` service. See [services/README.md](services/README.md) and [services/room-snapshot-api/README.md](services/room-snapshot-api/README.md) for deployment guides.

### Configuration
```bash
CAMERA_API_URL=http://flai-room-snapshot-api:5000
CAMERA_ENABLED=true
CAMERA_API_TIMEOUT=15
CAMERA_CHECK_INTERVAL=30
```

### Camera Permissions
In Admin Panel → Users tab, assign camera codes:
`tam` (tambour/entry), `pri` (hallway), `kor` (corridor), `spa` (bedroom),
`kab` (office/study), `det` (children's), `gos` (living room), `kuh` (kitchen), `bal` (balcony)

---

## 👥 User Management

### Admin Panel Features
| Feature | Description |
|---------|-------------|
| 👤 User Operations | Create, edit, delete user accounts |
| 🔑 Password Management | Reset passwords for any user |
| 🔐 Camera Permissions | Grant/revoke camera access per user |
| 🤖 Model Management | Configure GGUF models per module type |
| 📊 System Stats | Monitor database and storage sizes |
| 🎚️ Service Classes | Set queue priority (0=highest, 2=lowest) |

### CLI Commands
```bash
# Set admin password
docker exec flai-web flask admin-password NewPassword123

# View help
docker exec flai-web flask --help
```

---

### 💾 Backup & Restore

FLAI includes a built-in backup system accessible from the Admin Panel → **Backups** tab.

**Backup Types:**
- **Users only:** Backs up the `users` table only (user accounts, permissions, settings).
- **Full:** Backs up all data: users, chat sessions, messages, documents, uploaded files, and model configurations.

**Operations:**
- **Create:** Select the backup type and click «Create backup». The archive is saved to `data/db_backups/`.
- **Restore:** Click «Restore» on a backup file to replace the current database and files with the backup content. *Warning: This overwrites existing data.*
- **Download:** Download the backup archive to your local machine.
- **Delete:** Remove old backup files.

Backup files are stored as `.tar.gz` archives containing SQL dumps and file directories. Restoration requires confirmation and is logged for audit purposes.

---

## 🔍 Monitoring & Health

### Health Check Endpoint
```bash
curl http://localhost:5000/health
```

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2026-04-08T23:00:00.000000+00:00",
  "services": {
    "web": "ok",
    "database": "ok",
    "redis": "ok",
    "llamacpp": "ok"
  }
}
```

### Prometheus Metrics
```bash
curl http://localhost:5000/metrics
```

---

## 🗺️ Roadmap

### ✅ Completed

- **llama.cpp router mode** (`--models-dir`) — single llama-server with dynamic model switching
- **stable-diffusion.cpp** — Z-Image-Turbo for generation, Flux.2 Klein 4B for editing
- **OpenAI-compatible API** (`/v1/chat/completions`, `/v1/embeddings`)
- **Multimodal support** via mmproj in subdirectories
- **Dynamic model switching** with `--models-max 1`
- **Individual model parameters** via `models-preset.ini`
- **GGUF model management** via admin panel — configure models per module (chat, reasoning, multimodal, embedding) directly from the web interface
- **All translations updated** for llama.cpp and llama-swap terminology (EN + RU)
- **Piper TTS optimization** for large text synthesis — chunked processing with seamless audio transitions
- **llama-swap backend** — dynamic model management and GPU VRAM optimization, auto-generated config from DB
- **Voice features** — Whisper ASR (faster_whisper) speech-to-text + Piper TTS with male/female voices in EN/RU
- **Image generation & editing** — create images from text via Z_image_turbo, edit uploaded images via Flux.2 Klein 4B
- **RAG document search** — upload PDF/DOC/DOCX/TXT, vector search via Qdrant with configurable chunking
- **Camera integration** — request snapshots from IP cameras, analyze with multimodal models, granular user permissions
- **Backup & restore** — full or users-only backups from the admin panel (pg_dump + tar.gz archives)
- **Admin CLI tools** — `admin-password` for password reset, `cleanup-uploads` for orphaned file removal
- **Health check & metrics** — `/health` endpoint with service status, `/metrics` for Prometheus
- **Response style selector** — dropdown in chat header: neutral, academic, professional, friendly, funny
- **Repeat penalty** — `repeat_penalty` parameter (1.0–2.0) per model, prevents response loops
- **PostgreSQL 18 upgrade** — migrated from 16 to 18 with zero data loss
- **Service prefix formatting** — voice, camera, image gen/edit messages show bold prefix; excluded from TTS and clipboard
- **Message format migration** — all old service messages converted to structured JSON `{prefix, text}` format
- **SSE real-time delivery** — queue results and new messages delivered via Server-Sent Events (Redis pub/sub), replacing all HTTP polling
- **Static cache-busting** — all JS/CSS assets served with `?v=timestamp` to prevent stale cache after updates
- **PDF extraction via pdftotext** — accurate text positioning for complex PDF layouts (hh.ru resumes, tables, multi-column)
- **Real-time document indexing SSE** — document list auto-refresh when indexing completes or fails, no manual page reload needed
- **CLI command** — `flask migrate-messages-format` to convert old plain-text service messages to JSON format (supports `--dry-run`)
- **SSE reliability** — 4 root cause fixes for voice message delivery (lightning icon visibility, reconnect recovery, `user_id` passthrough for `message_new` events)
- **Migration `--add-emojis`** — `flask migrate-messages-format --add-emojis` to retroactively add `🎨` to existing image service messages (supports `--dry-run`)
- **Tablet responsive layout** — media query for 769–1199px fixes footer overlap with chat input caused by `100vh` vs `100%` mismatch in mobile browsers
- **Image streaming fix** — tokens after `[-IMAGE-EDIT-]` marker no longer discarded during SSE streaming, eliminating empty edit query errors
- **GPU/CPU auto-detect for SD** — `sd_wrapper.py` detects CUDA inside container via `nvidia-smi`; omits CPU offload flags on GPU; no `--cuda`; automatic CPU fallback
- **SD error translations restored** — `_sd_error_translation_markers()` in `utils.py` for pybabel extraction; 8 stale `.po` keys reactivated with proper source references
- **Session switching UI fix** — `chat-sessions.js`: `loadMessages()` called after server-side session deletion; same-session click re-fetches messages
- **Full i18n coverage** — all user-facing errors wrapped in `_()`/`gettext()`; 14 new translation keys; rule added to `AGENTS.md`
- **Audio ⚡ race condition fix** — `clearSessionQueue` + `fetchQueueStatus` race fixed for HTTP audio responses without `request_id`; single-session ⚡ indicator
- **Page-refresh recovery** — ⚡, streaming, and final response survive F5 during generation; `onStreamToken`/`onResultCompleted` handle missing `pendingRequestIds`
- **VRAM monitor & model auto-degradation** — background polling via `nvidia-smi` every 60s; progressive model degradation (100%→0% n_gpu_layers in 4 steps) on OOM; per-VRAM-tier safety caps
- **VRAM calculator in admin panel** — `/admin/api/model-estimate` endpoint estimates VRAM (weights + KV cache + compute); auto-calculated `n_gpu_layers` slider
- **SD progressive offload system** — 4-level offload (0=full GPU → 3=full CPU); VRAM headroom check (500MB) before generation
- **Live token/s speed display** — real-time tokens-per-second during streaming; final token/s in message header; `completion_tokens` stored in DB
- **Model config cache fix** — TTL cache replaced with `updated_at`-based versioning, eliminating cross-worker inconsistency with gunicorn `workers=2`
- **Architecture display fix** — numpy byte-string decoding (`[113 119 101 110 51]` → `qwen3`) in admin panel
- **GGUF metadata expansion** — `parameter_count`, `head_count`, `head_count_kv`, `key_length`, `value_length` scanned and stored in DB

### 🔄 In Progress
- Long-term dialog memory (cross-session context)
- Advanced RAG: metadata filtering, hybrid search
- Mobile-responsive UI optimizations

### 📅 Planned
- Plugin architecture for custom modules
- Multi-GPU support
- Advanced queue prioritization
- User activity analytics

---

## 📦 Models, Licenses and Sizes

### LLM Models (llama.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Qwen3-4B-Instruct-2507-Q4_K_M** | Chat (fast responses) | [Qwen License](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF) | ~2.5 GB |
| **gpt-oss-20b-Q4_K_M** | Reasoning (complex tasks) | [OpenAI License](https://huggingface.co/unsloth/gpt-oss-20b-GGUF) | ~12 GB |
| **Qwen3VL-8B-Instruct-Q4_K_M** | Multimodal (image analysis) | [Qwen License](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF) | ~5 GB + mmproj ~1.1 GB |
| **bge-m3-Q8_0** | Embedding (RAG) | [MIT License](https://huggingface.co/gpustack/bge-m3-GGUF) | ~0.6 GB |

### Image Generation Models (stable-diffusion.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Z-Image-Turbo (z_image_turbo-Q8_0)** | Image generation | [Model-specific](https://huggingface.co/bartowski/Z-Image-Turbo-GGUF) | ~6.2 GB |
| **ae.safetensors** (VAE) | Variational autoencoder for Z-Image | [Model-specific](https://huggingface.co/bartowski/Z-Image-Turbo-GGUF) | ~0.3 GB |
| **Qwen3-4B-Instruct-2507-Q4_K_M** | Text encoder for Z-Image | [Qwen License](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF) | ~2.5 GB *(shared with chat)* |

### Image Editing Models (stable-diffusion.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Flux.2 Klein 4B (flux-2-klein-4b-Q8_0)** | Image editing (change colors, remove objects, stylize) | [Flux License](https://huggingface.co/black-forest-labs/FLUX.2-Klein-dev) | ~4.5 GB |
| **flux2_ae.safetensors** | VAE for Flux.2 editing | [Flux License](https://huggingface.co/black-forest-labs/FLUX.2-dev) | ~0.3 GB |

### Voice Models

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **en_US-ryan-medium** | English TTS (male) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 MB |
| **en_US-ljspeech-medium** | English TTS (female) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 MB |
| **ru_RU-dmitri-medium** | Russian TTS (male) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 MB |
| **ru_RU-irina-medium** | Russian TTS (female) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 MB |
| **Whisper medium** | Speech recognition | [MIT (OpenAI)](https://github.com/openai/whisper) | ~1.5 GB |

### Total Download Sizes (Approximate)

| Configuration | Approx. Download |
|---------------|-----------------|
| Chat only (Qwen3-4B) | ~2.5 GB |
| Chat + Reasoning | ~14.5 GB |
| Chat + Multimodal | ~8 GB |
| Full LLM stack | ~22 GB |
| + Image generation | ~28 GB |
| + Image editing | ~31 GB |
| + Voice (TTS + Whisper) | ~35 GB |

> **Note**: After downloading models, FLAI works completely offline. No external scripts or modules are loaded at runtime.

---

## 🧪 Testing

FLAI includes comprehensive testing for all key components and load testing for the web interface.

### Unit Tests

```bash
# Install test dependencies
pip install -e ".[test]"

# Run all tests
pytest

# Run with coverage report
pytest --cov=app --cov=modules --cov-report=html

# Run by marker
pytest -m unit                           # unit tests only (no external deps)
pytest -m "not slow"                     # skip slow tests
pytest -m "not (requires_db or requires_redis)"  # skip DB/Redis tests

# Run specific test file
pytest tests/test_backups.py
pytest tests/test_admin_routes.py
pytest tests/test_sd_cpp_module.py
pytest tests/test_queue.py
pytest tests/test_security.py
pytest tests/test_resource_manager.py
pytest tests/test_llama_swap_config.py
pytest tests/test_validators.py
pytest tests/test_model_config.py
```

> **Note**: `tests/conftest.py` uses an in-memory mock database by default (no PostgreSQL required). In CI, a real PostgreSQL is available via the `DATABASE_URL` env variable.

### Load Testing

Load tests use [Locust](https://locust.io/) to simulate concurrent users.

```bash
# Install Locust (if not already installed)
pip install locust

# Web interface — open http://localhost:8089
locust -f tests/load/locustfile.py --host http://localhost:5000

# Headless mode — 10 users, spawn 2/sec, run 1 minute
locust -f tests/load/locustfile.py --headless -u 10 -r 2 --run-time 1m

# Using the convenience script
./tests/load/run_load_test.sh --host http://localhost:5000 --users 10 --spawn-rate 2 --run-time 1m
```

See [tests/load/README.md](tests/load/README.md) for detailed load testing instructions.

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 Models Used

| Model | Purpose | License | Size |
|-------|---------|---------|------|
| Qwen3-4B-Instruct-2507-Q4_K_M | Chat | Apache 2.0 | ~2.5 GB |
| gpt-oss-20b-Q4_K_M | Reasoning | Apache 2.0 | ~12 GB |
| Qwen3VL-8B-Instruct-Q4_K_M | Multimodal | Apache 2.0 | ~5 GB + mmproj ~1.1 GB |
| bge-m3-Q8_0 | Embedding | MIT | ~0.6 GB |
| Z-Image-Turbo (z_image_turbo-Q8_0) | Image Generation | Apache 2.0 | ~6.2 GB |
| Flux.2 Klein 4B (flux-2-klein-4b-Q8_0) | Image Editing | Apache 2.0 | ~4.5 GB |

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.

---

<br>
<div align="center"> Made with ❤️ for the local AI community</div>
