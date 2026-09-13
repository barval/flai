<div align="center">
  <img src="docs/logo.png" alt="Fully Local AI (FLAI)" width="200">

  # Fully Local AI (FLAI)

  **FLAI — a multifunctional, fully local, privacy-first AI platform.**

  [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
  [![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
  [![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?logo=docker&logoColor=white)](https://www.docker.com/)

[English](README.md) | [Russian](README-ru.md)
</div>

### 🎬 Video Overview

<p align="center">
  <a href="https://gitea.prits.top/barval-my/flai/raw/branch/master/docs/flai_english.mp4">
    <img src="docs/flai_english.png" width="720" alt="FLAI video overview">
  </a>
</p>

---

## ✨ Features

### 🤖 Core AI Capabilities
- 💬 **Intelligent Chat** – smart request routing (fast models for simple queries, powerful models for complex reasoning)
- 🛠 **Tool Calling** – native OpenAI-compatible tool calling: calculator, current time, date/time calculations, web search, document search (RAG), camera snapshots — all via llama.cpp `--jinja` + Qwen3
- 🌐 **Web Search** – real-time internet search via self-hosted SearXNG metasearch engine: news, weather, exchange rates, prices, latest events
- 🧠 **Advanced Reasoning** – dedicated model for calculations, code generation, creative writing (streaming responses)
- 🔍 **Multimodal Analysis** – upload images and ask questions about their content (llama.cpp + mmproj)
- 🎨 **Image Generation** – create images from text using stable-diffusion.cpp with automatic prompt optimization
- ✏️ **Image Editing** – upload an image and ask to edit it (Flux.2 Klein 4B model: change colors, remove objects, stylize)
- 🎬 **Video Generation** – create short videos from text or image+text prompts using LTX-Video 2B (distilled, 8-step inference)
- 🎤 **Voice Transcription** – convert voice messages to text using Whisper ASR (faster_whisper)
- 🗣️ **Text-to-Speech** – hear responses spoken aloud via Piper or Kokoro TTS (backend selectable at deploy time)
- 🧠 **Long-term Memory** – cross-session, persistent memory via SuperLocalMemory (SLM). CPU-only, rule-based fact extraction and merging (no LLM). Semantic deduplication via embeddings

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
- 🤖 **Model Management** – select and configure GGUF models for multimodal, reasoning, and embedding directly from the admin panel
- 💾 **Backup & Restore** – create and restore full or user-only backups directly from the admin interface
- 🖥 **Hardware Overview** – first admin tab showing compute platform (`nvidia`/`amd`/`intel`/`cpu`), GPU name, VRAM (total/available), CPU cores, and RAM (total/available)
- 📈 **System Monitoring** – view database sizes and system statistics
- 🔧 **CLI Tools** – manage admin password via Flask CLI command

---

## 🏗️ Architecture

FLAI is a modular Flask application that orchestrates self-hosted AI services built on the llama.cpp ecosystem.

### What's New in v11.2

| Feature | Notes |
|---------|-------|
| **Selectable TTS backend: Piper (default) or Kokoro** | Voice deployment now chooses ONE backend: `--with-voice-piper` (Piper — lightweight, ~0.2 GB models) or `--with-voice-kokoro` (Kokoro — higher quality, ~6 GB RAM). `--with-voice` is kept as an alias for Piper. Compose profiles: `with-voice-piper` / `with-voice-kokoro` (both include Whisper ASR). Deploy scripts download only the selected backend's models and toggle the matching `.env` URL. |
| **Kokoro-82M TTS engine** | New `services/kokoro/` — Flask HTTP API wrapping Kokoro-82M (82M params, ElevenLabs-level quality): `POST /tts`, `GET /health`, `GET /voices`. Docker image: python:3.11-slim + torch CPU. |
| **Russian voices with correct stress** | Studio-actor voices from `zaakirio/kokoro-ru`: `sveta` (female, WER 2.50% vs Piper 4.38%), `dima` (male). Lexical stress via RUAccent + acute-aware espeak-ng data (за́мок vs замо́к), ё restoration, vowel reduction (akanye) and orthoepic rules (солнце→сонце, final –ого→-ово). |
| **English voices** | `af_heart` (US female), `am_liam` (US male) from `hexgrad/Kokoro-82M`. |
| **Dual-backend TTS module** | `modules/tts.py` uses whichever backend URL is active (`KOKORO_URL` or `PIPER_URL`); `/api/tts/synthesize` accepts an optional `voice` name alongside `lang`/`gender`. |

### Core Components

| Component | Purpose | Technology | Default Port |
|-----------|---------|------------|--------------|
| **Flask Web** | Web interface, routing, API | Python | 5000 |
| **llama-swap** | Dynamic LLM model routing & management (llama.cpp proxy) | Go + llama.cpp | 8080 |
| **stable-diffusion.cpp** | Image generation (Z_image_turbo) and editing (Flux.2 Klein 4B) | C++ + CUDA | 7861 |
| **LTX-Video** | Video generation (text-to-video / image+text-to-video) | Python + PyTorch | 7872 |
| **Whisper ASR** | Speech-to-text transcription | faster_whisper | 9000 |
| **Piper / Kokoro TTS** | Text-to-speech synthesis (backend selectable at deploy: Piper default, Kokoro higher quality) | ONNX + Piper / Kokoro-82M | 8888 |
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

FLAI ships with two deployment modes:

- **GPU mode (NVIDIA)** — full-speed inference on CUDA GPUs. The whole stack (llama.cpp, stable-diffusion.cpp, LTX-Video) runs with CUDA builds and the NVIDIA Container Toolkit. This is the primary, recommended mode.
- **CPU-only mode** — the same feature set runs entirely on the CPU (LLM, image, and video generation). Everything is slower, but no GPU is needed at all.

> ⚠️ **AMD and Intel GPUs are not supported by the official compose stack.** The prebuilt images are CUDA-only (`llama-swap:cuda`, CUDA versions of sd.cpp and LTX). Unofficial ROCm (AMD) or Vulkan (AMD/Intel) builds of llama.cpp could work outside this project, but they are not covered by FLAI's resource manager, VRAM accounting, or deployment scripts. If you have an AMD/Intel GPU and want guaranteed behaviour, run the **CPU-only mode** instead.

### Hardware Tiers

| Component | Tier 1 (Minimal) | Tier 2 (Moderate) | Tier 3 (Full) | CPU-only |
|-----------|-----------------|-------------------|---------------|----------|
| **GPU VRAM** | 8 GB | 12 GB | 16+ GB | — (no GPU) |
| **RAM (minimum)** | 16 GB | 24 GB | 24 GB | 24 GB |
| **RAM (recommended)** | 24 GB | 32 GB | 32 GB | 48 GB |
| **CPU** | 4+ cores | 6+ cores | 6+ cores | 8+ cores (12 recommended) |
| **Storage** | 60 GB | 80+ GB SSD | 100+ GB SSD NVMe | 100+ GB SSD NVMe |

> **RAM budget (how it was calculated):** in GPU mode only *one* llama.cpp model lives in memory at a time (llama-swap unloads the previous one), so system RAM holds the operating system + PostgreSQL/Redis + the web app (~6–8 GB) plus a safety margin. Reasoning on an 8 GB GPU requires partial CPU offload of layer weights, which adds ~10 GB of RAM for the in-RAM layers. Video generation runs in a separate container and needs its own headroom — on GPU that is modest, on CPU it dominates:
>
> | Mode | RAM without video | RAM with video generation |
> |------|-------------------|---------------------------|
> | 8 GB GPU | 16–24 GB | 24–32 GB |
> | 12 GB GPU | 24–32 GB | 32–40 GB |
> | 16 GB GPU | 24–32 GB | 32–48 GB |
> | CPU-only | 24–32 GB | 48 GB (64 GB for 240-frame clips) |
>
> Voice features are opt-in (`--with-voice-piper` / `--with-voice-kokoro`): Whisper ASR adds ~1 GB RAM, plus the chosen backend — Piper up to ~0.5 GB (voices lazy-loaded per use) or Kokoro ~1.6 GB idle and up to ~6 GB during a Russian phrase (its 6 GB container limit). The CPU-only video numbers assume the LTX-Video container may use up to 64 GB (its memory cap in `docker-compose.cpu.yml`); the pre-flight planner gates generation on the *smaller* of host free RAM and that cap, so a 768×512×240 clip (~53 GB peak) genuinely needs a 64 GB host.

#### What works at each tier

| Feature | 8 GB | 12 GB | 16+ GB | CPU-only |
|---------|------|-------|--------|----------|
| Chat + Multimodal (Qwen3VL) | ✅ Qwen3VL-8B (~5.9 GB incl. mmproj) | ✅ Qwen3VL-8B | ✅ Qwen3VL-8B | ⚠️ ~3.7 tok/s |
| Reasoning | ⚠️ Qwen3.6-35B partial offload (~15–20 tok/s) | ✅ Qwen3.6-35B-A3B (~70–90 tok/s) | ✅ Qwen3.6-35B-A3B (106 tok/s) | ⚠️ ~9.5 tok/s |
| Image gen (SD) | ✅ up to 1024×1024 | ✅ up to 1536×1024 | ✅ up to 1536×1024 | ⚠️ slower |
| Image edit (Flux) | ✅ up to 768px long side | ✅ up to 1024px long side | ✅ up to 1024px long side | ⚠️ slower |
| Video gen (LTX-Video) | ⚠️ 512×512×120 frames | ✅ 768×512×240 frames | ✅ 768×512×240 frames | ⚠️ adaptive memory cascade (384×256×120 → 256×192×57) |
| Voice (Whisper + TTS) | ✅ CPU | ✅ CPU | ✅ CPU | ✅ CPU |
| RAG (Qdrant) | ✅ | ✅ | ✅ | ✅ |
| SLM long-term memory | ✅ CPU | ✅ CPU | ✅ CPU | ✅ CPU |

> **VRAM management:** All LLM models (multimodal, reasoning, embedding) share VRAM via llama-swap — only one is loaded at a time. SD and LTX-Video use separate GPU contexts with automatic LLM unload before generation. The system dynamically adjusts `n_gpu_layers` based on available VRAM. **CPU-only video:** before generation the worker checks free RAM (`MemAvailable`) against the LTX-Video container's own memory cap (`LTX_VIDEO_RAM_LIMIT_MB`); if the requested 768×512×240 does not fit it progressively degrades to 384×256×120 @ 12 fps, then 256×192×57 @ 6 fps, notifying the user with the exact chosen format and stopping with a clear message when even the smallest step is impossible.

### Model Benchmarks (RTX 5060 Ti 16 GB)

All numbers are **synthetic `llama-bench` measurements** (llama.cpp build 10603) on an RTX 5060 Ti 16 GB (Blackwell, 448 GB/s): Flash Attention on, q4_0 KV cache, all layers on GPU (`-ngl -1`). Two metrics are reported: **Prompt (pp512)** — throughput for processing a 512-token prompt — and **Generation (tg128)** — throughput for generating 128 tokens, averaged over 3 repetitions after a warmup run. File sizes are the GGUF file sizes. Real-world throughput differs: the FLAI system prompt and chat history enlarge the prompt, and llama-swap shares VRAM between loaded models.

| Model | Type | Quant | File | Prompt (pp512) | Generation (tg128) | Notes |
|-------|------|-------|------|----------------|--------------------|-------|
| **Qwen3.6-35B-A3B** | Reasoning | UD Q2_K_XL (MoE) | 11.44 GiB | 1594 t/s | **107.5 t/s** | **Current reasoning model** — MoE 35B (3B active) |
| gpt-oss-20b | Reasoning | MXFP4 (MoE) | 11.27 GiB | 2052 t/s | 120.1 t/s | Fast but outdated — MoE 3B active |
| gemma-4-26B-A4B-it | Reasoning | UD Q2_K_XL (MoE) | 9.81 GiB | 2749 t/s | 104.5 t/s | MoE 26B (4B active) |
| Qwen3-4B-Instruct-2507 | Reasoning | Q4_K_M | 2.32 GiB | 5520 t/s | 119.0 t/s | Dense 4B — SD text encoder |
| Ternary-Bonsai-27B | Reasoning | Q2_g64 | 7.05 GiB | 993 t/s | 43.3 t/s | Ternary 27B |
| gemma-4-12B-it-qat | Reasoning | QAT Q4_K_XL | 6.24 GiB | 2310 t/s | 48.3 t/s | Dense 12B |
| Qwen3.8-27B | Reasoning | UD Q2_K_XL | 9.14 GiB | 763 t/s | 33.2 t/s | Dense 27B |
| Muse-Glimmer-30B | Reasoning | UD Q2_K_XL | 11.58 GiB | 664 t/s | 26.1 t/s | Dense 30B |
| **Qwen3VL-8B-Instruct** | Multimodal | Q4_K_M | 4.68 GiB | 3343 t/s | **74.8 t/s** | **Current multimodal model** — fastest vision model |
| bge-m3-Q8_0 | Embedding | Q8_0 | 0.60 GiB | 31500 t/s | 550 t/s | Embedding (RAG) only |

> **Current stack: CPU vs GPU (the three models FLAI uses by default).**

The CPU column was measured **live on the current server** (12-core CPU-only deployment, llama.cpp CPU builds, `n_gpu_layers=0`). The 16 GB column was measured on an RTX 5060 Ti 16 GB (Blackwell, 448 GB/s). The 8/12 GB columns are **estimates** for typical cards of that class — real throughput scales with the card's memory bandwidth and generation, so treat them as guidance, not guarantees.

| Model | Role | File | CPU 12C (measured) | GPU 8 GB* | GPU 12 GB* | GPU 16 GB (measured) |
|-------|------|------|--------------------|-----------|-----------|----------------------|
| **Qwen3VL-8B-Instruct-Q4_K_M** | Multimodal (chat/router/vision) | 4.7 GB + mmproj 1.1 GB | **3.7 tok/s** (generation) | 25–35 tok/s | 45–60 tok/s | **73.1 tok/s** |
| **Qwen3.6-35B-A3B-UD-Q2_K_XL** | Reasoning | 12 GB | **9.5 tok/s** (generation) | 15–20 tok/s (partial CPU offload) | 70–90 tok/s | **106.2 tok/s** |
| **bge-m3-Q8_0** | Embedding | 0.6 GB | **~1020 tok/s** (warm, 20 ms/doc) | 1.5–2.5 k tok/s | 2.5–4 k tok/s | ~4 k tok/s |

> **Read the CPU row as follows:** a typical chat answer (~200 tokens) from the multimodal model takes ~55 s on CPU vs ~3 s on a 16 GB GPU; a reasoning answer takes ~21 s on CPU vs ~2 s on GPU. Embedding/vector indexing is the least affected (bge-m3 is small and fast even on CPU).

> **Why MoE models win as reasoning models:** Despite "20B+" parameters, these models use the Mixture-of-Experts (MoE) architecture with several experts — only a small number of parameters (~3B) is active per token. This gives the compute cost of a 3B model with the "knowledge" of a 20B+ model. MoE models are always faster than dense models of the same size.

> **Qwen3.6-35B-A3B for reasoning:** MoE architecture (35B total, ~3B active) delivers **107.5 t/s** — only 10% slower than gpt-oss-20b. The best option when gpt-oss-20b quality is not enough.

> **Why MTP doesn't help on 128-bit GPUs:** Multi-Token Prediction (MTP) predicts draft tokens with a small head, then verifies them in parallel. On high-bandwidth GPUs (256/512-bit), this yields 1.4–2.2× speedup. On RTX 5060 Ti's 128-bit bus (448 GB/s), the draft model's extra memory reads saturate the already-limited bandwidth. MTP accordingly provides no meaningful speedup over a plain Q4_K_M of the same size, so MTP variants are not used.

> **MXFP4 on Blackwell:** RTX 5060 Ti (Blackwell GB206) has 5th-gen Tensor cores with native FP4 hardware support. MXFP4 models achieve near-Q4_K_M quality at similar file sizes while benefiting from Blackwell's optimized FP4 pathways. 

### Software Prerequisites
- Linux server
- **GPU mode (NVIDIA):** NVIDIA drivers + **NVIDIA Container Toolkit** installed, plus an NVIDIA GPU with 8 GB+ VRAM
- **CPU-only mode:** no NVIDIA tooling required — plain Docker is enough
- Docker Engine ≥ 20.10
- Docker Compose ≥ 2.0
- Internet connection (only for initial model downloads)

> 💡 **Note**: After downloading GGUF models, FLAI works completely offline.

---

## 🚀 Quick Start

> 💡 **Note**: For GPU deployment, you must have the **NVIDIA drivers** and **NVIDIA Container Toolkit** installed.

### Option A: Automated Deployment (Recommended)

A single deployment script handles everything: environment setup, model downloads, building, and launching.

```bash
git clone https://github.com/barval/flai.git
cd flai

# Core multimodal + llama.cpp only
./deploy.sh --download-models

# + Image generation/editing
./deploy.sh --download-models --with-image-gen

# + Voice: Whisper ASR + TTS. Pick ONE backend:
#   --with-voice-piper    Piper TTS (default choice, lightweight, ~0.2 GB models)
#   --with-voice-kokoro   Kokoro TTS (higher quality, ~6 GB RAM)
#   (--with-voice is an alias for Piper)
./deploy.sh --download-models --with-image-gen --with-voice

# + RAG (Qdrant)
./deploy.sh --download-models --with-image-gen --with-voice --with-rag

# + Video generation (LTX-Video)
./deploy.sh --download-models --with-image-gen --with-voice --with-rag --with-video

# + Long-term memory (SuperLocalMemory)
./deploy.sh --download-models --with-image-gen --with-voice --with-rag --with-video --with-slm

# + Web search (SearXNG)
./deploy.sh --download-models --with-image-gen --with-voice --with-rag --with-video --with-slm --with-search

# Full stack
./deploy.sh --download-models --with-image-gen --with-voice --with-rag --with-video --with-slm --with-search

# Run tests after deployment
./deploy.sh --download-models --with-image-gen --run-tests
```

> **CPU-only deployment (no NVIDIA GPU):** add the `--cpu` flag. Nominal `docker-compose.cpu.yml` is selected automatically when `nvidia-smi` is not found, but `--cpu` forces it.
>
> ```bash
> ./deploy.sh --cpu --download-models --with-image-gen --with-voice --with-rag --with-video --with-slm --with-search
> ```

> **Environment keys are generated automatically:** the script copies `.env.example` to `.env` and fills in `SECRET_KEY` and `QDRANT_API_KEY` with secure random values itself — you only need to edit `.env` manually to set your timezone, API URLs, or other preferences. If the first run is interrupted after `.env` was created, re-running the same command skips reconfiguration and continues with the downloads.

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

#### LLM Models (multimodal, reasoning, embedding)

```bash
mkdir -p services/llamacpp/models

# Multimodal model (chat/router/vision, always resident) — must be in subdirectory with mmproj
mkdir -p services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/Qwen3VL-8B-Instruct-Q4_K_M.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf"
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/mmproj-F16.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-8B-Instruct-F16.gguf"

# Reasoning model (complex tasks)
wget -O services/llamacpp/models/Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf \
  "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf"

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

> **Note**: Since v10.0 there is no standalone chat model. The only Qwen3-4B copy in the project is the SD text encoder (`Qwen3-4B-Instruct-2507-Q4_K_M.gguf` in `services/sd_cpp/models/text_encoders/`), required by stable-diffusion.cpp for image generation/editing.

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

# With voice features — pick ONE backend:
#   --profile with-voice-piper    Piper TTS (default)
#   --profile with-voice-kokoro   Kokoro TTS (higher quality)
#   (--profile with-voice is an alias for Piper)
docker compose -f docker-compose.gpu.yml --profile with-voice-piper up -d

# With video generation
docker compose -f docker-compose.gpu.yml --profile with-video up -d

# With long-term memory (SuperLocalMemory)
docker compose -f docker-compose.gpu.yml --profile with-slm up -d

# With web search (SearXNG)
docker compose -f docker-compose.gpu.yml --profile with-search up -d

# Full stack: multimodal + images + voice + RAG + video + long-term memory + web search
docker compose -f docker-compose.gpu.yml --profile with-image-gen --profile with-voice-piper --profile with-rag --profile with-video --profile with-slm --profile with-search up -d
```

> ⏱️ **First build takes time**: stable-diffusion.cpp is compiled from source (~5-10 minutes). Subsequent builds use the cache.

#### CPU-only mode (no GPU required)

For systems without an NVIDIA GPU, use `docker-compose.cpu.yml` instead. It runs the **same full feature set** — just slower. Use `docker-compose.cpu.yml` in all the commands above (e.g. `docker compose -f docker-compose.cpu.yml up -d`). All timeout values are already increased for CPU speed.

> 🔄 **Switching between GPU and CPU is instant — no rebuild needed.** Image-generation and video images are tagged per backend and **coexist** in the local registry: `flai-sd_cpp:cuda` / `flai-sd_cpp:cpu` and `flai-ltxvideo:cuda` / `flai-ltxvideo:cpu`. Re-running `./deploy.sh` (GPU) or `./deploy.sh --cpu` (CPU) simply switches compose files and reuses the already-built image of the matching tag — ideal for quick CPU sanity checks even on a GPU machine.

> ⚠️ **AMD / Intel GPU owners:** the official images are CUDA-only, so use the CPU-only mode above. There is no supported ROCm/Vulkan path.

### 4. Set Admin Password

```bash
docker exec flai-web flask admin-password YourSecurePassword123
```

### 5. Configure Models in Admin Panel

1. Open `http://localhost:5000` and log in as `admin`
2. Go to **Admin Panel** → **Models** tab
3. For each module (Multimodal, Reasoning, Embedding):
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
- 🗣️ **Listen to Responses** — text-to-speech via Piper (default) or Kokoro TTS (male/female, EN/RU)
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
SERVICE_RETRY_ATTEMPTS=5
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

### Domain Access and HTTPS (Reverse Proxy)

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

    # Must be >= MAX_CONTENT_LENGTH_MB from .env (default 50 MB)
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

### Docker Configuration

**Gunicorn Settings (gunicorn_config.py):**

Configuration is loaded from `gunicorn_config.py`, not inline CLI args.

| Setting | Value | Reason |
|---------|-------|--------|
| workers | 1 | Single gunicorn worker — fixes `_gpu_lock` race condition (threading.Lock is per-process) |
| worker_class | gevent | Async I/O-optimized worker for concurrent connections |
| timeout | 900s | Accommodates long operations (image editing up to 15 min) |
| graceful_timeout | 30s | Graceful worker shutdown |
| keepalive | 5s | Connection reuse for health checks |

### Docker Compose Profiles

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

## 🤖 Model Setup

### GGUF Model Structure

llama.cpp runs in **router mode** (`--models-dir`), dynamically loading models from a shared directory:

```
services/llamacpp/models/
├── Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf             # Reasoning (all tiers)
├── bge-m3-Q8_0.gguf                            # Embedding
├── Qwen3VL-8B-Instruct-Q4_K_M/                 # Multimodal (subdirectory!) — chat/router/vision
│   ├── Qwen3VL-8B-Instruct-Q4_K_M.gguf
│   └── mmproj-F16.gguf                         # Vision projector
```

> ⚠️ **Multimodal models require a subdirectory** with the projector file named `mmproj-*.gguf` inside. The model server auto-discovers and loads it.

### Configure Models in Admin Panel

1. Log in as admin and go to `/admin` → **Models** tab
2. For each module (Multimodal, Reasoning, Embedding):
   - Select the GGUF model from the dropdown, set parameters, click **Save**

> 💡 **Changing the embedding model triggers automatic re-indexing** of all documents.

### Model Parameters

| Parameter | Multimodal | Reasoning | Embedding |
|-----------|------------|-----------|-----------|
| Context Length | 16384 | 16384 | 512 |
| Temperature | 0.7 | 0.7 | – |
| Top P | 0.9 | 0.9 | – |
| Repeat Penalty | 1.1 | 1.15 | – |
| Timeout (s) | 120 | 120 | 120 |

> **Note:** Router classification always uses `temperature=0.1` (hardcoded) for deterministic query routing, regardless of admin panel settings.

### Model Selection Guide

| Component | Default | Recommended Alternative | Notes |
|-----------|---------|------------------------|-------|
| **Chat/router/vision** | Qwen3VL-8B Q4_K_M (~5.5 GB) | — | Single multimodal model serves all three roles; always resident. Requires subdirectory with `mmproj-*.gguf` |
| **Reasoning** | Qwen3.6-35B-A3B Q2_K_XL (~12 GB) | gpt-oss-20b mxfp4/Q4_K_M (~12 GB) | MoE architecture: ~3B active params, ~106 tok/s. Current reasoning model on all tiers; 8 GB uses partial CPU offload |
| **Embedding** | bge-m3 Q8_0 (~1.5 GB) | — | Single model for all tiers |

> **Context windows:** Multimodal and reasoning models should use the same context length (recommended 16384). Multimodal needs ≥16384 for vision token counts.

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
3. Compiles with CUDA 13.0.1 (`cmake -DSD_CUDA=ON`) or without CUDA for CPU
4. Produces `sd-server` and `sd-cli` binaries

Each compose file builds **its own tagged image** via the `SD_BACKEND` build arg (see `Dockerfile.sd_cpp`):
- `docker-compose.gpu.yml` → `flai-sd_cpp:cuda` (`SD_BACKEND=cuda`)
- `docker-compose.cpu.yml` → `flai-sd_cpp:cpu` (`SD_BACKEND=cpu`; optional `vulkan` variant supported)

Because the tags differ, GPU and CPU images can live side by side — switching between the stacks (see «CPU-only mode» above) does not require rebuilding.

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
# Enable voice features (Whisper ASR; choose ONE TTS backend profile — with-voice-piper or with-voice-kokoro)
docker compose -f docker-compose.gpu.yml --profile with-voice-piper up -d
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

### Kokoro TTS

Higher-quality backend (ElevenLabs-level). Selected with `--with-voice-kokoro` / `--profile with-voice-kokoro`. Models are downloaded in one step by `services/kokoro/download-model.sh` (the deploy script runs it automatically):

```bash
bash services/kokoro/download-model.sh
```

### Choosing the backend: Piper vs Kokoro

| Criterion | Piper (default) | Kokoro |
|-----------|-----------------|--------|
| Model size on disk | ~0.24 GB (4 medium voices) | ~0.95 GB (3 model files + voices + espeak-data) |
| Service memory limit | 512 MB | 6 GB |
| Idle RAM (no TTS activity) | ~200–300 MB | ~1.6 GB (light worker, RUAccent not loaded) |
| RAM during active sessions | ~500 MB (all 4 voices cached) | ~3–5.5 GB (RUAccent + model in worker; peak during long phrases) |
| Russian quality | Good (WER 4.38%) | Higher (WER 2.50%, studio actors) |
| Russian voices | `dmitri` (male), `irina` (female) | `dima` (male), `sveta` (female) |
| Russian pronunciation | espeak-ng phonemes, no real word stress | RUAccent: lexical stress, ё restoration, akanye, orthoepy |
| First phrase (fresh container) | ~0.8 s | EN ~2.9 s; **RU ~10–11.5 s** |
| Subsequent phrases (same session) | ~0.7–0.8 s | RU ~1.1 s |
| Cold start after idle | none — voices stay cached | **RU only:** after ≥5 min without Russian the RUAccent G2P worker (~3.1 GB) is auto-killed to return RAM; the next Russian phrase reloads it (~10–11.5 s). EN is not affected. |

> **Memory notes (measured):** Piper caches every used voice in memory — with all 4 voices loaded it reaches ~497 MiB, close to its 512 MB limit. Kokoro's worker releases ~3.1 GB to the OS after 300 s of no Russian TTS, so a quiet period is followed by a single slower first Russian phrase (then ~1.1 s for subsequent ones).

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
- **Russian name recognition** – pymorphy3 morphological analysis generates all grammatical declensions (nominative, accusative, prepositional cases) for each room name, so the AI recognizes phrases like "show me the living room", "what is in the living room", "in the kitchen" etc.

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

### 🔄 In Progress
- **Multi-platform GPU support** — extend FLAI to run on non-NVIDIA machines:
  - CPU-only mode for the full stack
  - AMD / Intel via Vulkan for llama.cpp and stable-diffusion.cpp, ROCm for LTX-Video
  - Unified Docker Compose with per-platform profiles and env-driven deploy scripts
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
| **Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf** | Reasoning (all tiers) | [Qwen License](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF) | ~12 GB |
| **Qwen3VL-8B-Instruct-Q4_K_M** | Multimodal — chat/router/vision | [Qwen License](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF) | ~5.5 GB + mmproj ~1.1 GB |
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
| Minimal (Qwen3VL-4B + Qwen3.6-35B-A3B + bge-m3, 8 GB tier) | ~16 GB |
| Full LLM stack (Qwen3VL-8B + Qwen3.6-35B-A3B + bge-m3) | ~20 GB |
| + Image generation | ~29 GB |
| + Image editing | ~32 GB |
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
