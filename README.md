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

### What's New in v8.8

| v8.8+ (New) | Notes |
|-------------|-------|
| 🛡️ **5-layer model protection in admin panel** | 3-tier VRAM/RAM classification: 🟢 `good` (full ngl), 🟡 `cpu_offload` (auto-degraded ngl), 🔴 `impossible` (save blocked, 400), ⚠ `unknown` (no GGUF metadata — click "Refresh models" first). Server-side validation, background dry-load + auto-rollback, crash-loop watchdog. Prevents OOM and 502 on bad model saves. |
| 🛡️ **3-tier classification math** | `vram_needed ≤ 85% × total_vram` → good; otherwise `(file - gpu_weights) ≤ 70% × ram - 2 GB` → cpu_offload with recomputed ngl; else impossible (server returns 400). Upper ctx limit is now **dynamic** from `gguf_models_cache.context_length` (no hardcoded 32768). |
| 🔁 **Background dry-load after admin save** | New `app/tasks/dry_load.py` — after `signal_reload()` sends a tiny completion to llama-swap, polls `/running` (30 s timeout). On failure, rolls back to `FALLBACK_MODELS[module]`. Daemon thread. |
| 🐕 **Crash-loop watchdog** | New `app/tasks/health_monitor.py` — 60 s polling, sends health check to each running model, tracks failures in 5-min sliding window. **3 failures → auto-rollback to fallback**. Started from `create_app()` only in llama-swap mode. |
| 📊 **Real VRAM measurement + dynamic estimation** | New `model_vram_estimates` table. `measure_model_vram()` captures actual VRAM after each successful load. `get_vram_estimate()` / `upsert_vram_estimate()` helpers. Admin panel shows "✓ Measured (N) / ℹ Estimated" with color-coded percentage bars. |
| 🧮 **Dynamic VRAM estimation from GGUF metadata** | `_estimate_model_vram()` now uses `file_size_mb × (ngl / block_count) + ctx_size × kv_factor + overhead` — no hardcoded 2500/5000/15000/2000 MB constants. Reads from `gguf_models_cache` (block_count, file_size_mb, context_length). |
| 🔌 **Separate circuit breaker per model type** | `LlamaSwapBackend._get_circuit_breaker(model_type)` — chat, reasoning, multimodal, embedding each have their own CB. One model's OOM no longer blocks another. |
| ⚙️ **Adaptive model degradation on every failure** | `_record_llama_failure()` calls `_degrade_model_if_needed()` on every failure (not just when CB opens). `compute_llamacpp_config()` iteratively reduces ngl to fit available VRAM. |
| 🔁 **Reasoning 502 → retry with degrade** | `max_retries = 1` for `reasoning` and `chat` (was only `multimodal`). First failure → degrade ngl; second failure → user-facing error. |
| 🔒 **Fast worker now acquires `_gpu_lock`** | Previously only slow worker serialized GPU tasks. Chat, embedding, RAG search now also wait for GPU. Prevents parallel GPU tasks. |
| 🧠 **RAG: generation moved to slow worker** | `_process_rag_task*` no longer calls `rag.generate_answer()` directly. Fast worker does **only** `rag.search()` (embedding + Qdrant) and requeues to slow worker via `_requeue_reasoning_task(rag_context=...)`. Slow worker handles VRAM (unloads LTX-Video, loads reasoning). Prevents GPU contention. |
| 📝 **RAG prompt fix** | `rag.template` no longer says "answer on your own" or "don't write 'no info'". Now: *"use ONLY the provided context. If context doesn't contain the answer — honestly say you cannot find it."* Prevents hallucination. |
| 📦 **Raw Qdrant chunks → reasoning model** | When `rag.generate_answer()` returns None, `_process_reasoning_request` calls `rag.search()` directly and passes raw chunks as `rag_context` to the reasoning model. Reasoning model always sees document content. |
| 🗂️ **Multi-tab session support** | Client now sends `session_id` in request body (UUID v4). Server validates ownership and updates Flask session. Cookie race conditions between tabs fixed. `app/static/js/chat-init.js`, `app/routes/messages.py`, `app/db.py` updated. |
| 🧹 **Queue position: server data only** | Removed `pendingRequestIds` race guard from `chat-queue.js` that overwrote server positions with hardcoded `1`. Multiple ⚡ prevention preserved (only one session shows ⚡; rest show ⏳ with real positions). |
| ⚠️ **Error message prefix** | `_build_error_response()` adds `⚠️ ` prefix to all user errors. Helper `_is_llm_error_string()` routes `call_llamacpp()` error strings (e.g., "GPU memory unavailable", "HTTP error 500") through `_build_error_response()`. Applied in `_process_reasoning_request`, `_process_text_task*`, RAG. |
| 🌐 **Translation system fix** | Removed broken `.mo` volume mounts from `docker-compose.gpu.yml`. Docker now compiles all translations at build time. All site features work in both Russian and English. |
| 📺 **Video VRAM: try/finally + flush CUDA** | Both video task handlers wrap generation in `try/finally` — `_unload_video_pipeline()` and `_unload_llamacpp_models()` always run. CUDA cache flushed after generation. `_wait_for_vram_full` timeout 30 s → 60 s. Buffer +500 → +3000 MB. No more "proceeding anyway" on timeout. |
| 📐 **Dynamic video VRAM chain** | `estimate_video_vram_needed()`: 1) measured (from `model_vram_estimates`), 2) HTTP `/v1/vram_info` from ltx-wrapper (component sizes + current peak), 3) local filesystem (if `/app/models` mounted), 4) env fallback. New endpoint in `ltx_wrapper.py`. |
| 🗃️ **New DB tables** | `model_vram_estimates` (module, model_name, ctx, ngl, estimated_mb, measured_mb, measurement_count, last_measured_at). `slm_import_progress` (user_id, last_message_id, total_imported) for checkpoint-based background import. |
| 🧪 **55 new tests** | `tests/test_classify_model_fit.py` (11), `tests/test_dry_load.py` (10), `tests/test_health_monitor.py` (12), `tests/test_resource_manager_ltx_unload.py` (11), `tests/test_vram_estimates.py` (10). All passing. |
| 🧹 **Docker compose cleanup** | Removed `docker-compose.cpu.yml` (CPU-only mode unsupported — FLAI requires NVIDIA GPU). Removed `services/llamacpp/generate_presets.py` (obsolete). Removed `services/sd_cpp/Dockerfile.sd_cpp-cpu`. |
| 🤖 **Default chat model upgraded** | `Qwen3-4B-Instruct-2507-Q4_K_M` → `Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf` (~2 GB, faster routing). Default ctx 8192 → 16384 for both chat and reasoning. |
| 📦 **Deploy scripts: VRAM tier detection** | `deploy.sh` / `deploy-ru.sh` now detect GPU VRAM via `nvidia-smi` and auto-select reasoning model: 16 GB+ → `gpt-oss-20b-Q4_K_M`, 12 GB → `Qwen3-8B-Thinking-Q4_K_M`, 8 GB → `Qwen3-4B-Thinking`. |
| 🧠 **SLM daemon mode** | SuperLocalMemory now runs as a proper daemon (`slm serve start`) keeping the embedding model in memory permanently. Replaced the per-request `subprocess --sync` calls. SLM recall latency reduced from ~10 s to ~1 ms. HTTP proxy (`slm_http.py`) forwards requests to daemon internally. **Per-user isolation:** recall reads directly from the user's private SQLite, not from the daemon's shared database. |
| 🧠 **SLM context for both chat + reasoning** | SLM facts are now injected into prompts for BOTH chat and reasoning models (alongside full conversation history). Previously was reasoning-only with only 2 last messages. `SLM_RECALL_LIMIT=5` (default). |
| 🧠 **RAG fixes: router, streaming, context** | Router template now has dedicated category 5 for document/person/age queries → `[-RAG-]`. Streaming path (`_process_text_task_stream`) now calls RAG before requeuing to reasoning. Strict threshold lowered 0.7 → 0.5. Reasoning model receives document context via `{rag_context}`. RAG retry in `_process_reasoning_request`. |
| 🎮 **Video VRAM fix: multimodal unload confirmation** | Fixed `_wait_for_vram_full()` — changed from impossible ≥80% threshold to `video_needed + 3 GB` buffer. Timeout increased 30 → 60 s. No more "proceeding anyway" into OOM. |
| 🖼️ **Image display in streamed messages** | `finalizeStreamedMessage` renders images/videos from `result.file_path` / `result.file_data`. `file_data` added to `get_session_messages` SQL SELECT. `contextlib.suppress` replaced with proper logging in `db.py`. |
| 🧪 **Test isolation improvements** | `conftest.py`: `stop_workers(timeout=3)` in `test_app` teardown. `TRUNCATE` on real PostgreSQL between tests (in CI). |
| 🔨 **Various lint fixes** | SIM102, SIM108, F841 (3×), F821, N812, B904 — all resolved across `app/db.py`, `app/queue.py`, `modules/base.py`, `services/ltx_video/ltx_wrapper.py`. |

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
| Image edit (Flux) | ✅ up to 768px long side | ✅ up to 1024px long side | ��� up to 1024px long side |
| Video gen (LTX-Video) | ⚠️ 512×512×121 frames | ✅ 896×512×257 frames | ✅ 896×512×257 frames |
| Voice (Whisper + TTS) | ✅ CPU | ✅ CPU | ✅ CPU |
| RAG (Qdrant) | ✅ | ✅ | ✅ |
| SLM long-term memory | ✅ CPU | ✅ CPU | ✅ CPU |

> **VRAM management:** All LLM models (chat, reasoning, multimodal, embedding) share VRAM via llama-swap — only one is loaded at a time. SD and LTX-Video use separate GPU contexts with automatic LLM unload before generation. The system dynamically adjusts `n_gpu_layers` based on available VRAM.

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
  "https://huggingface.co/bartowski/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q8_0.gguf"

# VAE
wget -O services/sd_cpp/models/vae/ae.safetensors \
  "https://huggingface.co/bartowski/Z-Image-Turbo-GGUF/resolve/main/ae.safetensors"

# LLM text encoder (shared with chat)
wget -O services/llamacpp/models/Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf \
  "https://huggingface.co/bartowski/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf"
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

> The text encoder `Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf` is **shared** between generation and editing. Download it once.

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
| workers | 1 | Minimal RAM (+40MB); all requests wait for the same AI backend |
| threads | 4 | Handles 4 concurrent I/O-bound connections |
| worker_class | gthread | Optimal for waiting on AI responses |
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
| **LTX-Video 2B distilled** | 8 | 8–30 fps | up to 768×1344 | Distilled, single GPU (~6 GB VRAM) |

Video generation runs in a **separate GPU container** (via `--profile with-video`). Before generating, the llama.cpp LLM is automatically unloaded from VRAM to free memory. After generation, CUDA cache is cleared, LLM processes are re-unloaded, and the CUDA primary context is reset (`cuDevicePrimaryCtxReset`) to release all GPU memory back to the driver. The T5 text encoder (~8.9 GB in bf16) stays on CPU.

**Source image resize:** Images for video-from-image are resized to **896px** on the longest side before being sent to the LTX pipeline (reduces VRAM and network payload). A system notice shows the original vs resized dimensions.

**Aspect ratio matching:** When generating video from an image, the output video resolution is automatically adjusted to match the source image's aspect ratio: square images → 512×512, wide images (w/h > 1.2) → 896×512 landscape, tall images (w/h < 0.8) → 512×896 portrait.

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
| **bge-m3-Q8_0** | Embedding (RAG) | [MIT License](https://huggingface.co/gpustack/bge-m3-GGUF) | ~0.6 GB |

### Image Generation Models (stable-diffusion.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Z-Image-Turbo (z_image_turbo-Q8_0)** | Image generation | [Model-specific](https://huggingface.co/bartowski/Z-Image-Turbo-GGUF) | ~6.2 GB |
| **ae.safetensors** (VAE) | Variational autoencoder for Z-Image | [Model-specific](https://huggingface.co/bartowski/Z-Image-Turbo-GGUF) | ~0.3 GB |
| **Qwen3-4B-Instruct-2507-MXFP4_MOE.gguf** | Text encoder for Z-Image | [Qwen License](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF) | ~2 GB *(shared with chat)* |

### Image Editing Models (stable-diffusion.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Flux.2 Klein 4B (flux-2-klein-4b-Q8_0)** | Image editing (change colors, remove objects, stylize) | [Flux License](https://huggingface.co/black-forest-labs/FLUX.2-Klein-dev) | ~4.5 GB |
| **flux2_ae.safetensors** | VAE for Flux.2 editing | [Flux License](https://huggingface.co/black-forest-labs/FLUX.2-dev) | ~0.3 GB |

### Video Generation Models

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **ltxv-2b-0.9.8-distilled.safetensors** | LTX-Video 2B diffusion transformer + VAE | [LTX-Video License](https://huggingface.co/Lightricks/LTX-Video) | ~5.9 GB |
| **PixArt T5-XXL (text_encoder)** | T5 text encoder for LTX-Video | [PixArt License](https://huggingface.co/PixArt-alpha/PixArt-XL-2-1024-MS) | ~18 GB (disk, float32) |

### Long-term Memory Models

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **nomic-embed-text-v1.5** | Text embedding for SLM retrieval | [Apache 2.0](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) | ~500 MB |

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
