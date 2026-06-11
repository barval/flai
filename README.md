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
- 🧠 **Advanced Reasoning** – dedicated model for calculations, code generation, creative writing (streaming responses)
- 🔍 **Multimodal Analysis** – upload images and ask questions about their content (llama.cpp + mmproj)
- 🎨 **Image Generation** – create images from text using stable-diffusion.cpp with automatic prompt optimization
- ✏️ **Image Editing** – upload an image and ask to edit it (Flux.2 Klein 4B model: change colors, remove objects, stylize)
- 🎬 **Video Generation** – create short videos from text or image+text prompts using LTX-Video 2B (distilled, 8-step inference)
- 🎤 **Voice Transcription** – convert voice messages to text using Whisper ASR (faster_whisper)
- 🗣️ **Text-to-Speech** – hear responses spoken aloud via Piper TTS (male and female voices in English and Russian)
- 🧠 **Long-term Memory** – cross-session, persistent memory via SuperLocalMemory (SLM). CPU-only, zero-LLM retrieval. Adds relevant facts alongside conversation history. Enable with `--profile with-slm`.

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
- 🎤 **Combined Voice + Image** – record voice message while an image is attached; both sent together
- 🔔 **Notifications** – unread message indicators and blinking status icons for processing/queued requests
- ⏹ **Task Cancellation** – cancel any in-progress streaming generation with a single click
- 📊 **Progress Bars** – visual progress indicators for video, image, and reasoning generation
- 📋 **Copy Messages** – one-click copy of full assistant message text
- ▶ **Run HTML** – execute HTML code blocks directly from chat in a new browser tab
- 🛡 **XSS Protection** – all markdown HTML sanitized via DOMPurify before rendering

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

### What's New in v8.9

| v8.9+ (New) | Notes |
|-------------|-------|
| **Video: 240 frames @ 24 fps** | Default video length increased from 8s to 10s (240 frames @ 24fps). VRAM-capped mode: 120 frames @ 24fps (5 sec) |
| **Video: 768×512 resolution** | Landscape video resolution reduced from 896×512 to 768×512 for better VRAM headroom on 16GB GPUs |
| **3-tier model protection** | Admin panel blocks saving models that won't fit in VRAM/RAM (🟢 good / 🟡 cpu_offload / 🔴 impossible / ⚠ unknown) |
| **Dry-load + auto-rollback** | After saving a model config, background test verifies it loads. On failure → auto-rollback to fallback model |
| **Crash-loop watchdog** | Monitors llama-swap models every 60s. 3 failures in 5 min → auto-rollback to fallback |
| **RAG on slow worker** | RAG generation moved to slow worker (was on fast worker, caused GPU contention with LTX-Video) |
| **RAG context in reasoning** | Reasoning model now receives document context from RAG search |
| **Multi-tab session fix** | Client sends `session_id` in request body; server validates ownership. No more cookie race conditions |
| **Streaming reasoning** | Reasoning model now streams responses token-by-token instead of returning the full response at once |
| **Generation progress bars** | Visual progress indicators for video, image, and reasoning generation via SSE events |
| **Task cancellation** | Cancel any in-progress streaming task with the `■` button in real time |
| **Thinking tag filtering** | Automatic removal of `<tool_call>` and `<\|channel\|>` reasoning blocks from model output (client + server) |
| **Camera rooms CRUD** | Full camera management in admin panel: sync from API, enable/disable, thumbnail previews |
| **Russian morphological analysis** | pymorphy3 for recognizing all grammatical declensions of camera room names in queries |
| **Combined voice + image** | Record voice message while an image is already attached — both sent together |
| **DOMPurify XSS protection** | All markdown HTML sanitized before DOM insertion to prevent XSS attacks |
| **Lazy loading images** | Images and videos in messages load lazily for faster initial rendering |
| **Run HTML button** | Execute HTML code blocks directly from chat in a new browser tab |
| **Copy message text** | One-click copy of full assistant message text |
| **Stream recovery** | Progress bars and streaming state restored after page reload or SSE reconnect |
| **MTP factor in VRAM estimation** | Multi-Token Prediction draft layers (+15% VRAM) accounted for in model fit calculations |
| **GGUF fallback reading** | Admin panel reads model metadata directly from GGUF files when cache is empty |
| **Qwen3-4B MXFP4 migration** | Auto-migration of old chat models to Qwen3-4B-Instruct-2507-MXFP4_MOE |
| **Dead code cleanup** | Removed unused functions (`get_gguf_model_info`, `find_gguf_file`, `chunk_text_by_sentences`, etc.) and CSS classes |
| **Faster retries** | llama.cpp retry sleep reduced from 5s to 2s; VRAM polling from 1s to 0.5s |
| **Chat export includes videos** | Generated videos are now embedded in exported HTML files as base64 |

### Core Components

| Component | Purpose | Technology | Default Port |
|-----------|---------|------------|--------------|
| **Flask Web** | Web interface, routing, API | Python | 5000 |
| **llama-swap** | Dynamic LLM model routing & management (llama.cpp proxy) | Go + llama.cpp | 8080 |
| **stable-diffusion.cpp** | Image generation (Z_image_turbo) and editing (Flux.2 Klein 4B) | C++ + CUDA | 7861 |
| **LTX-Video** | Video generation (text-to-video / image+text-to-video) | Python + PyTorch | 7872 |
| **Whisper ASR** | Speech-to-text transcription | faster_whisper | 9000 |
| **Piper TTS** | Text-to-speech synthesis | ONNX + Piper | 8888 |
| **Qdrant** | Vector database for RAG | Rust | 6333 |
| **SuperLocalMemory** | Long-term, cross-session memory per-user (daemon + HTTP proxy) | Python + SQLite | 8766 |
| **Redis** | Request queue management | C | 6379 |
| **PostgreSQL** | User accounts, sessions, messages | SQL | 5432 |
| **Resource Manager** | Adaptive GPU/CPU/RAM management, prevents OOM errors, coordinates GPU access | Python |
| **Circuit Breaker** | Prevents cascading failures by blocking calls to failing services (llama.cpp, sd.cpp, Whisper) after repeated errors | Python |

### Single-Server Architecture

All services run on one machine with GPU sharing:

```text
┌───────────────────────────────────────────────────────────────┐
│                       FLAI Web (Flask)                        │
│           Redis Queue → Model Router → Response               │
└──────┬──────────┬────────────┬──────────────┬─────────────────┘
       │          │            │              │
       ▼          ▼            ▼              ▼
   llama-swap  sd.cpp      LTX-Video    Whisper/Piper/Qdrant
   :8080       :7861       :7872        (separate containers)
   (dynamic model routing via llama-swap)
```

**Dynamic Model Routing**: llama-swap acts as a proxy to llama.cpp, dynamically loading/unloading GGUF models on demand. Only one model occupies VRAM at a time, with automatic switching based on request type. Model configuration is managed via the admin panel and stored in the database.

> 🎬 **Video generation** uses a **separate GPU container** (`ltxvideo`) with its own VRAM context. Before each video generation, the llama.cpp LLM model is automatically unloaded from VRAM to free memory for the video pipeline (transformer + VAE ≈ 6 GiB). After generation, CUDA cache is cleared, LLM processes are re-unloaded, and the pipeline is reset (`_pipeline = None`) for lazy reinit on the next request. The T5 text encoder stays on CPU to conserve VRAM.

---

## 📋 System Requirements

### GPU Requirement

FLAI **requires** an NVIDIA GPU with CUDA support. CPU-only mode is not supported — LLM inference, image generation, and video generation all depend on CUDA.

### Hardware Tiers

| Component | Tier 1 (Minimal) | Tier 2 (Moderate) | Tier 3 (Full) |
|-----------|-----------------|-------------------|---------------|
| **GPU VRAM** | 8 GB | 12 GB | 16+ GB |
| **RAM** | 16 GB | 16 GB | 16 GB |
| **CPU** | 4+ cores | 4+ cores | 6+ cores |
| **Storage** | 60 GB | 80+ GB SSD | 100+ GB SSD NVMe |

#### What works at each tier

| Feature | 8 GB | 12 GB | 16+ GB |
|---------|------|-------|--------|
| Chat (Qwen3-4B) | ✅ full speed | ✅ full speed | ✅ full speed |
| Reasoning | ⚠️ Qwen3-4B-Thinking (~2.5 GB) | ✅ Qwen3-8B-Thinking (~5 GB) | ✅ gpt-oss-20b (~12 GB, ngl=16+) |
| Multimodal | ⚠️ Qwen3VL-4B (~2.5 GB) recommended | ✅ Qwen3VL-8B (~5.5 GB) | ✅ Qwen3VL-8B (~5.5 GB) |
| Image gen (SD) | ✅ up to 1024×1024 | ✅ up to 1536×1024 | ✅ up to 1536×1024 |
| Image edit (Flux) | ✅ up to 768px long side | ✅ up to 1024px long side | ✅ up to 1024px long side |
| Video gen (LTX-Video) | ⚠️ 512×512×120 frames | ✅ 768×512×240 frames | ✅ 768×512×240 frames |
| Voice (Whisper + TTS) | ✅ CPU | ✅ CPU | ✅ CPU |
| RAG (Qdrant) | ✅ | ✅ | ✅ |
| SLM long-term memory | ✅ CPU | ✅ CPU | ✅ CPU |

> **VRAM management:** All LLM models (chat, reasoning, multimodal, embedding) share VRAM via llama-swap — only one is loaded at a time. SD and LTX-Video use separate GPU contexts with automatic LLM unload before generation. The system dynamically adjusts `n_gpu_layers` based on available VRAM.

### Model Benchmarks (RTX 5060 Ti 16 GB)

Real-world performance measured with llama.cpp (llama-swap on-demand loading, Flash Attention, q4_0 KV cache):

| Model | Type | Quant | Size | VRAM | Prompt | Generation | Notes |
|-------|------|-------|------|------|--------|------------|-------|
| **Qwen3-4B-Instruct-2507** | Chat | MXFP4 (MoE) | 2.0 GB | 3668 MB | 1307 t/s | **127.4 t/s** | Current chat model |
| Qwen3.5-4B-Instruct-MTP | Chat | MXFP4 + MTP | 2.5 GB | 4042 MB | 664 t/s | 108.2 t/s | MTP adds overhead |
| **gpt-oss-20b** | Reasoning | MXFP4 (MoE) | 11.5 GB | 12255 MB | 1100 t/s | **120.5 t/s** | Current reasoning model |
| Qwen3.5-9B-MTP-Q4_K_M | Reasoning | Q4_K_M + MTP | 5.5 GB | 6717 MB | 431 t/s | 66.1 t/s | 45% slower |
| Qwen3.5-9B-Q8_0 | Reasoning | Q8_0 | 8.9 GB | 9719 MB | 472 t/s | 42.8 t/s | 65% slower |
| gemma-4-12B-it-qat | Reasoning | QAT Q4_K_XL | 6.3 GB | 8090 MB | 520 t/s | 49.2 t/s | 59% slower |

> **Why gpt-oss-20b wins as reasoning model:** Despite being a "20B" model, gpt-oss-20b uses Mixture-of-Experts (MoE) with 32 experts — only ~3B parameters are active per token. This gives it 3B-level compute cost with 20B-level knowledge. On RTX 5060 Ti, it generates **120 tok/s** vs 66 tok/s for dense Qwen3.5-9B — nearly **2× faster** while using the same memory bandwidth.

> **Why MTP doesn't help on 128-bit GPUs:** Multi-Token Prediction (MTP) predicts draft tokens with a small head, then verifies them in parallel. On high-bandwidth GPUs (256/512-bit), this yields 1.4–2.2× speedup. On RTX 5060 Ti's 128-bit bus (448 GB/s), the draft model's extra memory reads saturate the already-limited bandwidth. Qwen3.5-4B-MTP is **15% slower** than Qwen3-4B without MTP; Qwen3.5-9B-MTP shows no meaningful speedup over a plain Q4_K_M of the same size.

> **MXFP4 on Blackwell:** RTX 5060 Ti (Blackwell GB206) has 5th-gen Tensor cores with native FP4 hardware support. MXFP4 models achieve near-Q4_K_M quality at similar file sizes while benefiting from Blackwell's optimized FP4 pathways. 

### Software Prerequisites
- Linux server with **NVIDIA GPU** (CUDA support required)
- **NVIDIA drivers** installed on host
- **NVIDIA Container Toolkit** installed
- Docker Engine ≥ 20.10
- Docker Compose ≥ 2.0
- Internet connection (only for initial model downloads)

> 💡 **Note**: After downloading GGUF models, FLAI works completely offline.

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

# + Video generation (LTX-Video)
./deploy.sh --download-models --with-image-gen --with-voice --with-rag --with-video

# + Long-term memory (SuperLocalMemory)
./deploy.sh --download-models --with-image-gen --with-voice --with-rag --with-video --with-slm

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
wget -O services/llamacpp/models/Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf \
  "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf"

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
  "https://huggingface.co/leejet/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q8_0.gguf"

# VAE
wget -O services/sd_cpp/models/vae/ae.safetensors \
  "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors"

# LLM text encoder (for SD, separate copy with Q4_K_M quantization)
wget -O services/sd_cpp/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
```

#### Image Editing Models (Flux.2 Klein 4B)

```bash
# Diffusion model for editing
wget -O services/sd_cpp/models/diffusion_models/flux-2-klein-4b-Q8_0.gguf \
  "https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q8_0.gguf"

# VAE for editing
wget -O services/sd_cpp/models/vae/flux2_ae.safetensors \
  "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors"
```

> The text encoder for SD (`Qwen3-4B-Instruct-2507-Q4_K_M.gguf`) is a separate copy downloaded to `services/sd_cpp/models/text_encoders/`. The chat model (`Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf`) in `services/llamacpp/models/` is a different quantization.

> ⚠️ **Important**: Multimodal models **must** be placed in a subdirectory named after the model, with the `mmproj-*.gguf` file inside. The llama.cpp router automatically discovers and loads the projector.

#### Video Generation Models (LTX-Video 2B)

```bash
# Create models directory
mkdir -p services/ltx_video/models

# Diffusion transformer + VAE checkpoint
wget -O services/ltx_video/models/ltxv-2b-0.9.8-distilled.safetensors \
  "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltxv-2b-0.9.8-distilled.safetensors"

# T5 text encoder (run the download script)
bash services/ltx_video/download-t5-encoder.sh
```

### 3. Build and Start Services

```bash
# Chat and reasoning only (no image generation)
docker compose -f docker-compose.gpu.yml up -d

# With image generation
docker compose -f docker-compose.gpu.yml --profile with-image-gen up -d

# With voice features
docker compose -f docker-compose.gpu.yml --profile with-voice up -d

# With video generation
docker compose -f docker-compose.gpu.yml --profile with-video up -d

# With long-term memory (SuperLocalMemory)
docker compose -f docker-compose.gpu.yml --profile with-slm up -d

# Full stack: chat + images + voice + RAG + video + long-term memory
docker compose -f docker-compose.gpu.yml --profile with-image-gen --profile with-voice --profile with-rag --profile with-video --profile with-slm up -d
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
- 🎬 **Generate Videos** — create short videos from text or image+text prompts
- 🎤 **Send Voice Messages** — speech-to-text via Whisper ASR
- 🗣️ **Listen to Responses** — text-to-speech via Piper TTS (male/female, EN/RU)
- 📚 **Search Documents** — upload PDF/DOC/TXT and ask questions (RAG)
- 🗂️ **Multiple Chat Sessions** — separate conversations with auto-titling
- 💾 **Export Chats** — save conversations as HTML with embedded media
- 📹 **View Cameras** — IP camera snapshots analyzed by AI
- 🧠 **Long-term Memory** — cross-session memory via SuperLocalMemory (adds relevant facts alongside history, enable with `--with-slm`)
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
 LTX_VIDEO_WRAPPER_URL=http://flai-ltxvideo:7872  # LTX-Video video generation
 SLM_URL=http://flai-slm:8766                      # SuperLocalMemory long-term memory
 ```

**Image & Video Defaults:**
```bash
SD_CPP_DEFAULT_WIDTH=1024
SD_CPP_DEFAULT_HEIGHT=1024
SD_CPP_DEFAULT_CFG_SCALE=1.0    # 1.0 for flow-matching models (Z_image_turbo)
SD_CPP_DEFAULT_STEPS=10         # 10 for Z_image_turbo
SD_CPP_TIMEOUT=900
MAX_IMAGE_SIZE=1536             # Resize uploaded images to 1536px on longest side
LTX_VIDEO_TIMEOUT=600           # Max video generation time (seconds)
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
| workers | 2 | One for SSE streaming, one for regular requests |
| worker_class | gevent | Async I/O-optimized worker for concurrent connections |
| timeout | 900s | Accommodates long operations (image editing up to 15 min) |
| graceful_timeout | 30s | Graceful worker shutdown |
| keepalive | 5s | Connection reuse for health checks |

### Docker Compose Profiles

```bash
# Start all services (chat + images + voice + RAG + video + long-term memory)
docker compose -f docker-compose.gpu.yml --profile with-image-gen --profile with-voice --profile with-rag --profile with-video --profile with-slm up -d

# Chat + voice only
docker compose -f docker-compose.gpu.yml --profile with-voice up -d

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

## 🤖 Model Setup

### GGUF Model Structure

llama.cpp runs in **router mode** (`--models-dir`), dynamically loading models from a shared directory:

```
services/llamacpp/models/
├── Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf     # Chat
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
| Context Length | 16384 | 16384 | 8192 | 512 |
| Temperature | 0.1 | 0.7 | 0.7 | – |
| Top P | 0.1 | 0.9 | 0.9 | – |
| Repeat Penalty | 1.1 | 1.15 | 1.1 | – |
| Timeout (s) | 120 | 120 | 120 | 120 |

---

## 🎨 Image Generation & Editing

### Generation Model

The project uses **Z_image_turbo** as the only image generation model:

| Model | Steps | CFG Scale | Resolution | Notes |
|-------|-------|-----------|------------|-------|
| **Z_image_turbo** | 10 | 1.0 | up to 1536×1536 | Fast, flow-matching |

All uploaded images are automatically resized to **1536px** on the longest side (configurable via `MAX_IMAGE_SIZE` in `.env`) to prevent Qwen3VL context overflow and reduce disk usage.

Configure via `SD_MODEL_TYPE` in `.env`:
```bash
SD_MODEL_TYPE=z_image_turbo
```

### Image Editing (Flux.2 Klein 4B)

Upload an image and ask to edit it (e.g., *"change the pupils to green"*, *"remove the second sun"*). The system uses:
1. **Multimodal model** (Qwen3VL) to analyze the image and generate an edit prompt
2. **Flux.2 Klein 4B** model via stable-diffusion.cpp to perform the edit
3. The original image is preserved except for the requested changes

Source images for editing are automatically resized to **1024px** on the longest side to avoid OOM on 16GB GPUs. A system notice shows the original vs resized dimensions if downscaled.

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

## 🎬 Video Generation (LTX-Video 2B)

The project uses **LTX-Video 2B 0.9.8 distilled** for video generation:

| Model | Steps | Frame Rate | Resolution | Notes |
|-------|-------|-----------|------------|-------|
| **LTX-Video 2B distilled** | 8 | 24 fps | up to 768×1344 | Distilled, single GPU (~6 GB VRAM) |

Video generation runs in a **separate GPU container** (via `--profile with-video`). Before generating, the llama.cpp LLM is automatically unloaded from VRAM to free memory. After generation, CUDA cache is cleared, LLM processes are re-unloaded, and the CUDA primary context is reset (`cuDevicePrimaryCtxReset`) to release all GPU memory back to the driver. The T5 text encoder (~8.9 GB in bf16) stays on CPU.

**Source image resize:** Images for video-from-image are resized to **768px** on the longest side before being sent to the LTX pipeline (reduces VRAM and network payload). A system notice shows the original vs resized dimensions.

**Aspect ratio matching:** When generating video from an image, the output video resolution is automatically adjusted to match the source image's aspect ratio: square images → 512×512, wide images (w/h > 1.2) → 768×512 landscape, tall images (w/h < 0.8) → 512×768 portrait.

**Required models:**
1. `ltxv-2b-0.9.8-distilled.safetensors` (~5.9 GB) — diffusion transformer + VAE
2. `PixArt-alpha/PixArt-XL-2-1024-MS` text encoder / tokenizer — T5-XXL encoder (~18 GB on disk in float32, ~8.9 GB in VRAM in bf16)

```bash
# Download via deploy script
./deploy.sh --download-models --with-video

# Or manually:
bash services/ltx_video/download-t5-encoder.sh
```

**Configuration:**
```bash
LTX_VIDEO_WRAPPER_URL=http://flai-ltxvideo:7872
LTX_VIDEO_MODEL=ltxv-2b-0.9.8-distilled
LTX_VIDEO_TIMEOUT=600
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

### Camera Management (Admin Panel)
The admin panel includes a **Cameras** tab with full CRUD operations:
- **Sync** – import camera list from room-snapshot-api (`/rooms` endpoint)
- **Enable/Disable** – toggle individual cameras on/off
- **Thumbnail previews** – lazy-loaded camera snapshots with localStorage caching
- **Russian name recognition** – pymorphy3 morphological analysis generates all grammatical declensions (именительный, винительный, предложный падежи) for each room name, so the AI recognizes "покажи гостиную", "что в гостиной", "на кухне" etc.

Camera room data is stored in the `camera_rooms` database table (code, name_forms, enabled, sort_order).

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
- **llama-swap backend** — dynamic model management, auto-generated config from DB, GPU VRAM optimization
- **OpenAI-compatible API** (`/v1/chat/completions`, `/v1/embeddings`)
- **Multimodal support** — mmproj in subdirectories, image analysis via Qwen3VL
- **GGUF model management** via admin panel — configure models per module (chat, reasoning, multimodal, embedding) from the web interface
- **Image generation & editing** — Z_image_turbo for generation, Flux.2 Klein 4B for editing
- **Video generation (LTX-Video 2B)** — text-to-video and image+text-to-video, separate GPU container
- **Voice features** — Whisper ASR (faster_whisper) speech-to-text + Piper TTS with male/female voices in EN/RU
- **RAG document search** — PDF/DOC/DOCX/TXT upload, vector search via Qdrant with configurable chunking
- **SuperLocalMemory (SLM)** — long-term cross-session memory, daemon mode, per-user SQLite isolation, ~1 ms recall latency
- **RAG: generation on slow worker** — fast worker does only search, reasoning model generates answer; prevents GPU contention; RAG prompt uses ONLY context (no hallucination)
- **5-layer model protection in admin panel** — 3-tier VRAM/RAM classification (🟢 good / 🟡 cpu_offload / 🔴 impossible / ⚠ unknown), server-side validation, background dry-load + auto-rollback, crash-loop watchdog
- **Camera integration** — IP camera snapshots, multimodal analysis, granular user permissions
- **Backup & restore** — full or users-only backups from admin panel (pg_dump + tar.gz)
- **Multi-language support** — full interface and AI responses in Russian and English
- **VRAM management** — `ensure_vram_for_llm()`, auto-unload LTX-Video before SD/video, VRAM freed between every GPU task
- **Dynamic VRAM estimation** — computed from GGUF metadata (file_size, block_count, ctx) + real measurements stored in DB; admin panel shows color-coded percentage bars
- **Adaptive model degradation** — iterative `n_gpu_layers` reduction on OOM, per-model-type circuit breakers, reasoning 502 retry with degrade
- **GPU requirement + 3 hardware tiers** — auto-detect VRAM via `nvidia-smi` in deploy scripts (8/12/16+ GB)
- **Video VRAM hardening** — try/finally in both video handlers, CUDA flush, timeout 60 s, buffer +3000 MB, no "proceeding anyway"
- **SSE real-time delivery** — queue results and messages via Server-Sent Events (Redis pub/sub), replacing HTTP polling
- **Video via slow queue** — video tasks re-queued from fast worker, serialized GPU access
- **Fast worker GPU lock** — chat, embedding, RAG search also acquire `_gpu_lock`, preventing parallel GPU tasks
- **Live token/s speed display** — real-time tokens-per-second during streaming, final speed in message header
- **Response style selector** — dropdown in chat header: neutral, academic, professional, friendly, funny
- **Repeat penalty** — `repeat_penalty` parameter (1.0–2.0) per model
- **Chat loading optimization** — base64 `file_data` stripped from API response (~1000× reduction)
- **Unified image resize (1536px)** — prevents Qwen3VL context overflow, reduces disk usage
- **Static cache-busting** — all JS/CSS served with `?v=timestamp`
- **Translation system fix** — Docker compiles translations at build time; all features work in both languages
- **Router response parsing fix** — prevents copied template text and history markers from polluting queries
- **Multi-tab session support** — client sends `session_id` in request body, server validates ownership; no cookie race conditions
- **CUDA context cleanup after video** — `_pipeline = None` + `empty_cache()` + `gc.collect()` (safe, no SIGSEGV)
- **PostgreSQL 18** — migrated from 16 with zero data loss
- **TTL-based VRAM optimization** — non-chat models unload immediately (TTL=0s), chat stays hot (600s)
- **PDF extraction via pdftotext** — accurate text positioning for complex layouts (resumes, tables, multi-column)
- **Background SLM import on startup** — incremental import with checkpoint table, daemon thread, CLI: `flask import-history-to-slm`
- **Piper TTS optimization** — chunked processing for large text synthesis with seamless audio transitions
- **llama-swap v217** — Blackwell (sm_120) crash fixes
- **Default chat model upgraded** — Qwen3-4B MXFP4_MOE (~2 GB), default ctx 8192 → 16384
- **Deploy scripts: VRAM tier detection** — auto-select reasoning model: 16 GB+ → gpt-oss-20b, 12 GB → Qwen3-8B-Thinking, 8 GB → Qwen3-4B-Thinking
- **CLI tools** — `admin-password`, `cleanup-uploads`, `migrate-messages-format` (with `--dry-run`, `--add-emojis`)
- **Health check & metrics** — `/health` endpoint with service status, `/metrics` for Prometheus
- **File size display** — shown in chat headers for all file types
- **Video 240 frames @ 24fps** — default video length 10s (was 8s), VRAM cap 120 frames (5s)
- **Video 768×512 resolution** — landscape resolution reduced from 896×512 for better VRAM headroom
- **3-tier model protection** — admin panel blocks impossible models, dry-load + auto-rollback, crash-loop watchdog
- **RAG on slow worker** — prevents GPU contention with LTX-Video pipeline
- **Multi-tab session fix** — session_id in request body, server validates ownership
- **Streaming reasoning** — reasoning model streams responses token-by-token with real-time display
- **Generation progress bars** — visual progress for video, image, and reasoning tasks via SSE
- **Task cancellation** — cancel any in-progress streaming generation in real time
- **Thinking tag filtering** — automatic removal of `<tool_call>` and `<|channel|>` blocks from model output
- **Camera rooms CRUD** — dynamic camera management in admin panel with sync from API
- **Russian morphological analysis** — pymorphy3 for recognizing all declensions of room names
- **Combined voice + image** — record voice while image is attached; both sent together
- **DOMPurify XSS protection** — all markdown HTML sanitized before rendering
- **Stream recovery** — progress bars and streaming state restored after page reload or SSE reconnect
- **Run HTML button** — execute HTML code blocks from chat in a new browser tab
- **Copy message text** — one-click copy of full assistant response
- **Lazy loading images** — images and videos load lazily for faster initial rendering
- **Chat export includes videos** — generated videos are now embedded as base64 in exported HTML files

### 🔄 In Progress
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
| **Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf** | Chat (fast responses) | [Qwen License](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF) | ~2 GB |
| **gpt-oss-20b-Q4_K_M** | Reasoning (complex tasks) | [OpenAI License](https://huggingface.co/unsloth/gpt-oss-20b-GGUF) | ~12 GB |
| **Qwen3VL-8B-Instruct-Q4_K_M** | Multimodal (image analysis) | [Qwen License](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF) | ~5 GB + mmproj ~1.1 GB |
| **bge-m3-Q8_0** | Embedding (RAG) | [MIT License](https://huggingface.co/gpustack/bge-m3-GGUF) | ~1.5 GB |

### Image Generation Models (stable-diffusion.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Z-Image-Turbo (z_image_turbo-Q8_0)** | Image generation | [Apache 2.0](https://huggingface.co/leejet/Z-Image-Turbo-GGUF) | ~6.5 GB |
| **ae.safetensors** (VAE) | Variational autoencoder for Z-Image | [Apache 2.0](https://huggingface.co/Comfy-Org/z_image_turbo) | ~0.3 GB |
| **Qwen3-4B-Instruct-2507-Q4_K_M.gguf** | Text encoder for Z-Image | [Qwen License](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF) | ~2 GB |

### Image Editing Models (stable-diffusion.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Flux.2 Klein 4B (flux-2-klein-4b-Q8_0)** | Image editing (change colors, remove objects, stylize) | [Apache 2.0](https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF) | ~5 GB |
| **flux2_ae.safetensors** | VAE for Flux.2 editing | [Flux License](https://huggingface.co/Comfy-Org/flux2-dev) | ~0.3 GB |

### Video Generation Models

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **ltxv-2b-0.9.8-distilled.safetensors** | LTX-Video 2B diffusion transformer + VAE | [LTX-Video License](https://huggingface.co/Lightricks/LTX-Video) | ~5.9 GB |
| **PixArt T5-XXL (text_encoder)** | T5 text encoder for LTX-Video | [PixArt License](https://huggingface.co/PixArt-alpha/PixArt-XL-2-1024-MS) | ~18 GB (disk, float32) |

### Long-term Memory Models

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **nomic-embed-text-v1.5** | Text embedding for SLM retrieval | [Apache 2.0](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) | ~500 MB |

### Morphological Analysis

| Package | Purpose | License |
|---------|---------|---------|
| **pymorphy3** | Russian morphological analysis for camera room name recognition (generates declension forms) | [MIT License](https://github.com/kmike/pymorphy3) |

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
| + Video generation (LTX-Video + T5 encoder) | ~59 GB *(T5 encoder ~18 GB on disk in float32)* |
| + Long-term memory (SLM embedding model) | ~59.5 GB *(SLM adds ~500 MB)* |

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
pytest tests/test_resource_manager_ltx_unload.py
pytest tests/test_vram_estimates.py
pytest tests/test_classify_model_fit.py
pytest tests/test_dry_load.py
pytest tests/test_health_monitor.py
pytest tests/test_llama_swap_config.py
pytest tests/test_validators.py
pytest tests/test_model_config.py
pytest tests/test_morph.py
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

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.

---

<br>
<div align="center"> Made with ❤️ for the local AI community</div>
