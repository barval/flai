<div align="center">
  <img src="docs/logo.png" alt="Fully Local AI (FLAI)" width="200">

  # Fully Local AI (FLAI)

  **A multifunctional, fully local, privacy-first AI platform.**

  [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
  [![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
  [![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?logo=docker&logoColor=white)](https://www.docker.com/)

[English](README.md) | [Русский](README-ru.md)
</div>

## TL;DR

- **Everything runs on your hardware.** Chat, reasoning, image and video generation, voice, document search and web search — no cloud service in the path (optional Tavily web search is the only opt-in cloud service).
- **One GPU queue, strictly serialized.** VRAM is cleaned unconditionally between GPU tasks; a model is loaded only after the previous one is gone.
- **An LLM router decides what happens.** Every message is classified into one of eleven categories — there is no keyword routing anywhere in the code.
- **Multimodal, multilingual, persistent.** Images and scanned PDFs are read by a vision model, the UI is English/Russian, and long-term memory survives across sessions.
- **A public OpenAI-compatible API.** `/v1/chat/completions`, embeddings, media, audio, files and RLM, with per-user keys and an interactive reference at `/v1/docs`. Multi-image chat, deep analysis with images and «draw something similar» references work through the API exactly like in the web chat.
- **GPU or CPU.** 8 GB of VRAM runs the core stack; larger models and full-resolution video need more VRAM. CPU-only mode runs the same features, slower, with automatic downscaling for media.
- **One command to deploy.** `./deploy.sh --download-models --with-image-gen …` builds, downloads models and starts everything.

## 📑 Contents

<!-- TOC:BEGIN -->
- [TL;DR](#tldr)
- [📑 Contents](#-contents)
- [🆕 What's New in v12.5](#-whats-new-in-v125)
- [✨ Features](#-features)
- [🏗️ Architecture Overview](#-architecture-overview)
- [🧭 Types of Requests & Search Mechanisms](#-types-of-requests-search-mechanisms)
- [📋 System Requirements](#-system-requirements)
- [🚀 Quick Start](#-quick-start)
- [🛠️ Contributing](#-contributing)
- [🙏 Acknowledgments](#-acknowledgments)
- [📄 License](#-license)
<!-- TOC:END -->

### 📚 Full documentation

Every document exists in English and Russian (`X.md` / `X-ru.md`).

| Topic | English | Русский |
|-------|---------|---------|
| Architecture, queue, GPU lock, data flow | [ARCHITECTURE.md](docs/ARCHITECTURE.md) | [ARCHITECTURE-ru.md](docs/ARCHITECTURE-ru.md) |
| VRAM accounting, context auto-fit, hardware | [VRAM_MANAGEMENT.md](docs/VRAM_MANAGEMENT.md) | [VRAM_MANAGEMENT-ru.md](docs/VRAM_MANAGEMENT-ru.md) |
| Context window budget and allocation | [CONTEXT.md](docs/CONTEXT.md) | [CONTEXT-ru.md](docs/CONTEXT-ru.md) |
| Measured model throughput | [BENCHMARKS.md](docs/BENCHMARKS.md) | [BENCHMARKS-ru.md](docs/BENCHMARKS-ru.md) |
| Models, download commands, licenses | [MODELS.md](docs/MODELS.md) | [MODELS-ru.md](docs/MODELS-ru.md) |
| Environment variables, compose profiles | [CONFIGURATION.md](docs/CONFIGURATION.md) | [CONFIGURATION-ru.md](docs/CONFIGURATION-ru.md) |
| Web search, Tavily keys, site crawler | [SEARCH.md](docs/SEARCH.md) | [SEARCH-ru.md](docs/SEARCH-ru.md) |
| Deep analysis (RLM) | [DEEP_ANALYSIS.md](docs/DEEP_ANALYSIS.md) | [DEEP_ANALYSIS-ru.md](docs/DEEP_ANALYSIS-ru.md) |
| Image generation and editing | [IMAGE_GENERATION.md](docs/IMAGE_GENERATION.md) | [IMAGE_GENERATION-ru.md](docs/IMAGE_GENERATION-ru.md) |
| Video generation (LTX-Video) | [VIDEO.md](docs/VIDEO.md) | [VIDEO-ru.md](docs/VIDEO-ru.md) |
| Voice: Whisper, Piper, Kokoro | [VOICE.md](docs/VOICE.md) | [VOICE-ru.md](docs/VOICE-ru.md) |
| Camera integration | [CAMERA.md](docs/CAMERA.md) | [CAMERA-ru.md](docs/CAMERA-ru.md) |
| Long-term memory (SLM) | [MEMORY.md](docs/MEMORY.md) | [MEMORY-ru.md](docs/MEMORY-ru.md) |
| Documents, RAG, PDF OCR | [DOCUMENTS.md](docs/DOCUMENTS.md) | [DOCUMENTS-ru.md](docs/DOCUMENTS-ru.md) |
| Users, backups, Model Hub, branding | [ADMINISTRATION.md](docs/ADMINISTRATION.md) | [ADMINISTRATION-ru.md](docs/ADMINISTRATION-ru.md) |
| Health endpoint, Prometheus metrics | [MONITORING.md](docs/MONITORING.md) | [MONITORING-ru.md](docs/MONITORING-ru.md) |
| Public `/v1` API | [API.md](docs/API.md) | [API-ru.md](docs/API-ru.md) |
| Code layout, tests, CLI tools | [DEVELOPMENT.md](docs/DEVELOPMENT.md) | [DEVELOPMENT-ru.md](docs/DEVELOPMENT-ru.md) |
| Localization and translations | [LOCALIZATION.md](docs/LOCALIZATION.md) | [LOCALIZATION-ru.md](docs/LOCALIZATION-ru.md) |
| Troubleshooting | [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | [TROUBLESHOOTING-ru.md](docs/TROUBLESHOOTING-ru.md) |
| Frequently asked questions | [FAQ.md](docs/FAQ.md) | [FAQ-ru.md](docs/FAQ-ru.md) |
| Roadmap | [ROADMAP.md](docs/ROADMAP.md) | [ROADMAP-ru.md](docs/ROADMAP-ru.md) |
| Release process | [RELEASE_GUIDE.md](docs/RELEASE_GUIDE.md) | [RELEASE_GUIDE-ru.md](docs/RELEASE_GUIDE-ru.md) |
| How to contribute | [CONTRIBUTING.md](CONTRIBUTING.md) | [CONTRIBUTING-ru.md](CONTRIBUTING-ru.md) |

## 🆕 What's New in v12.5

- **Multi-attachment chat** — up to 4 images per message, sent to the vision model in one call (with a per-image fallback), and a document attached straight in chat is indexed into RAG so the model answers questions about it. Attachments render as chips: thumbnails for images, 🎤/🎵/📄 for voice/audio/documents. → [DOCUMENTS.md](docs/DOCUMENTS.md)
- **Attachments live on disk, sessions open instantly** — every attached file (images, audio, documents) is saved to disk from the very first write, and `flask backfill-attachment-paths` migrates older chats the same way, so a history reload no longer ships megabytes of base64 that once made heavy sessions slow to open (one picture-heavy message dropped from ~3.9 MB to a few KB). Built for old rows too: pre-migration messages keep rendering untouched. → [DEVELOPMENT.md](docs/DEVELOPMENT.md)
- **Deep analysis with attachments** — the 🔬 toggle accepts up to 4 images (each becomes a corpus «document») and chat-attached documents, so the Documents-panel selection is optional. → [DEEP_ANALYSIS.md](docs/DEEP_ANALYSIS.md)
- **«Draw something similar» with examples** — attach example images to an image-generation request; each example is described and the descriptions steer the SD prompt. An image-attached chat request («нарисуй что-то похожее») is routed to generation too, and the real width×height of the reference is respected. → [IMAGE_GENERATION.md](docs/IMAGE_GENERATION.md)
- **Higher limits** — documents 25 MB, per-user document storage 250 MB, request body 75 MB; scanned-PDF OCR gets a 30-minute budget per document with a partial-indexing notice. → [CONFIGURATION.md](docs/CONFIGURATION.md)
- **Tavily-first web search** — every user can attach a free personal Tavily key (1000 credits/month) in the profile popup. Search tries Tavily and degrades to the local SearXNG engine on any failure, so a spent quota or an invalid key never breaks search. → [SEARCH.md](docs/SEARCH.md)
- **Web crawler (Crawl4AI)** — an optional sidecar reads a pasted URL in a real browser (`read_page`) and, for an explicit «study this site» request, crawls the domain (≤50 pages, depth ≤3, 5-minute budget), replaces the per-domain document, indexes it through the normal RAG pipeline and answers from it — including in Deep Analysis. → [SEARCH.md](docs/SEARCH.md)
- **CPU timeouts scaled up** — CPU-only image generation gets 45 minutes and video 2 hours, so slow media generation finishes instead of dying at step 3/10. → [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- **A single version source** — the About dialog spells the project out and the version string comes from one constant. The exported chat repeats the site footer: the brand label opens the same About dialog, with the custom logo embedded in the header and the dialog. → [RELEASE_GUIDE.md](docs/RELEASE_GUIDE.md)
- **Reworked header and mobile UI** — on desktop the voice-gender and theme switchers sit before your user name with the logout button as the rightmost control; on mobile the Documents and Sessions panels open full-screen, the header is a single 38 px row with a compact «Ру ▾» language dropdown, and the voice/theme buttons live in the footer corners. A voice question recorded over an attached image is now transcribed with that image in sight (previously the model answered «there is no image in your message»). → [VOICE.md](docs/VOICE.md)

Full history: [CHANGELOG.md](CHANGELOG.md).

## ✨ Features

### 💬 Chat & reasoning
- **Intelligent chat** — the router sends simple questions to a fast model and complex ones to a dedicated reasoning model.
- **Native tool calling** — calculator, current time, date arithmetic, web search, document search, history search, page reading and camera snapshots, with live progress.
- **Deep analysis (RLM)** — a reasoning model works through your selected documents and an attached image step by step, with a sandboxed Python executor, sub-model calls, live lookups and a collapsible trace. → [DEEP_ANALYSIS.md](docs/DEEP_ANALYSIS.md)
- **Response styles** — neutral, academic, professional, friendly or funny, switchable in the chat header.
- **Sessions and export** — multiple auto-titled conversations, exportable as HTML with embedded media.

### 🎨 Media
- **Image generation** — stable-diffusion.cpp with automatic prompt optimization. → [IMAGE_GENERATION.md](docs/IMAGE_GENERATION.md)
- **Image editing** — upload an image and ask for changes: colors, removed objects, new styles.
- **Video generation** — LTX-Video 2B, 8-step distilled inference, text or image+text prompts. → [VIDEO.md](docs/VIDEO.md)

### 🔎 Search & knowledge
- **Web search** — self-hosted SearXNG metasearch plus optional Tavily, with page-content extraction when snippets are too thin. → [SEARCH.md](docs/SEARCH.md)
- **Document search (RAG)** — PDF, DOC, DOCX, TXT, ODT, RTF, CSV, JSON, EPUB, indexed in Qdrant; every indexed file is guaranteed a slot in the context. → [DOCUMENTS.md](docs/DOCUMENTS.md)
- **Document folders** — single-level folders in the Documents panel: create/rename/delete (with cascade), move documents by button or drag-and-drop, bulk selection with move and delete (the bar shows the total size), and upload straight into a folder. The same checkboxes feed the Deep Analysis corpus when the 🔬 toggle is on. → [DOCUMENTS.md](docs/DOCUMENTS.md)
- **Scanned PDF OCR** — pages with no extractable text are rendered and transcribed by the vision model before indexing.
- **Site reading and deep study** — open one URL or crawl a whole site into a searchable document.

### 🧠 Memory & context
- **Long-term memory (SLM)** — rule-based, CPU-only fact extraction and merging with semantic deduplication; a profile per user, erased with the account. → [MEMORY.md](docs/MEMORY.md)
- **Context budgeting** — real token costs, not guesses: SLM facts first, then search, then a rolling summary, then history. → [CONTEXT.md](docs/CONTEXT.md)
- **Per-request token counts** — every answer header shows the real output and input tokens. → [ARCHITECTURE.md](docs/ARCHITECTURE.md)

### 🗣️ Voice, camera & web UI
- **Voice messages** — Whisper ASR transcription.
- **Speech synthesis** — Piper (lightweight) or Kokoro (higher quality), selectable at deploy time, male or female voice. → [VOICE.md](docs/VOICE.md)
- **Camera snapshots (optional)** — request a frame from an IP camera and analyse it with the vision model, with per-user permissions. → [CAMERA.md](docs/CAMERA.md)
- **Attachments** — up to 4 images, one document, audio, or a voice message in one chat message; images attach as thumbnails (≤ the Send button height), documents/audio/voice as emoji chips. A document is indexed into RAG so the model can answer questions about it; several images are analysed together (compare/group questions). Clipboard paste (Ctrl+V) adds to the queue. → [DOCUMENTS.md](docs/DOCUMENTS.md)
- **Queue visibility** — live position and stage indicators, progress bars, task cancellation, unread markers.
- **HTML blocks** — run generated HTML from a message in a sandboxed preview tab.

### 🔌 API & automation
- **Per-user API keys** — created in the web UI, stored as SHA-256 digests, revocable.
- **OpenAI-compatible `/v1`** — chat completions (sync + SSE), async chat, embeddings, image and video generation, audio speech and transcription, files and documents, RLM, sessions and owner-scoped task polling. Multi-image chat requests, deep-analysis image attachments and image-generation references are accepted (up to 4 images, one joint vision call).
- **Interactive reference** — Swagger UI at `/v1/docs`, OpenAPI document at `/v1/openapi.json`, served offline with bundled assets. → [API.md](docs/API.md)

### ⚙️ Administration
- **Users** — create, edit, delete, service classes, camera permissions.
- **Models** — select and tune GGUF models per module; a context window that would not fit the hardware is rejected.
- **Model Hub** — search Hugging Face for GGUF models, see a VRAM/RAM fit estimate before downloading, download with progress and resume, delete what you no longer need.
- **Backups** — full or per-user backup and restore from the admin panel.
- **Personalization** — your own header logo and localized site names, included in full backups.
- **Monitoring** — hardware overview, database sizes, system statistics. → [MONITORING.md](docs/MONITORING.md)
- **CLI tools** — admin password, upload cleanup, message-format migration, SLM import and cleanup. → [DEVELOPMENT.md](docs/DEVELOPMENT.md)

### 🔒 Privacy & security
- 100% local processing; nothing leaves your network except the search services you configure.
- Session auth with password hashing, HttpOnly/SameSite cookies, CSRF on every form, login rate limiting and audit logging.
- Strict per-user isolation of sessions, messages, documents and camera access.
- Redis queue tasks are HMAC-signed; file paths are validated for traversal; all markdown HTML is sanitized with DOMPurify.
- Every URL that leaves the app passes an SSRF guard (no private, loopback, link-local or CGNAT addresses).

## 🏗️ Architecture Overview

```
Browser / API client
        │
        ▼
   Flask app (app/) ──► PostgreSQL   messages, sessions, users, documents
        │              Redis        queue, events (SSE), rate limits, task registry
        │              Qdrant       document vectors
        ▼
  Request queue (app/queue.py)
        │
        ├── fast worker (CPU) ──► routing, tools, RAG search, web search, STT, TTS, embeddings
        └── slow worker (GPU) ──► multimodal · reasoning · image · video      ← one at a time
                                  ▲
                          _gpu_lock (single)
                                  │
   modules/ ── llama.cpp (multimodal, reasoning, embedding) · sd.cpp · LTX-Video
          ── Whisper · Piper/Kokoro · SearXNG/Tavily/Crawl4AI · SuperLocalMemory
```

The rules that matter most:

1. **GPU work is strictly serialized.** Every GPU task takes the single `_gpu_lock`; after each one, all llama.cpp models are unloaded, the video pipeline is released and the CUDA cache is flushed.
2. **Degradation happens before loading.** `compute_llamacpp_config()` reduces `n_gpu_layers` until the model fits; if it does not fit even on CPU, the task fails with an error instead of an OOM crash.
3. **Retrieval never generates.** The fast worker only retrieves. Answers are produced by the reasoning model on the slow worker.
4. **Routing is always the LLM.** No keyword lists, no pattern matching, no hardcoded queries.

Details: [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [VRAM_MANAGEMENT.md](docs/VRAM_MANAGEMENT.md).

## 🧭 Types of Requests & Search Mechanisms

Every message is classified by the router model into one of these categories. The category decides what runs before the answer and what context is injected into the prompt.

| Category (marker) | Route | What runs | Injected into the prompt |
|---|---|---|---|
| `[-RAG-]` | **Document search** | Vector search in Qdrant (embeddings + score filter) | RAG chunks + SLM facts + session summary + history |
| `[-SEARCH-]` | **Web search** | Tavily first (per-user key), then SearXNG (parallel fetch, text extraction) | Web results (~30% of the budget) + SLM + summary + history |
| `[-HISTORY-]` | **History search** | Ranked PostgreSQL full-text search across prior sessions | History fragments + SLM + summary + current session |
| `[-REASONING-]` | **Complex reasoning** | The reasoning model directly, no external data | SLM facts + summary + history |
| `[-REASONING-WEB-]` | **Reasoning with fresh data** | Web search → reasoning over the results | Web results + SLM + summary + history |
| `[-CRAWL-]` | **Deep site study** | Crawl4AI crawls the site → replaces the domain document → indexes it → reasons over the corpus | Crawled pages as `## <url>` sections (budget-capped) + SLM + summary + history |
| `[-CAMERA-]` | **Camera snapshot** | Room-snapshot API grabs the current frame; the vision model analyses it | Snapshot description + SLM + history |
| `[-REMEMBER-]` | **Remember a fact** | Rule-based SLM extraction (background, CPU-only) | — (writes to long-term memory) |
| `[-IMAGE-]` / `[-VIDEO-]` | **Media generation** | stable-diffusion.cpp / LTX-Video in their own GPU containers | — (no LLM context) |
| `none` | **Chat with tools** | Multimodal model + native tool calling | Tool results + SLM + history |

**How it behaves in practice:**

- RAG, web and history search are mutually exclusive — one search mechanism runs per request.
- A message with **one** link asking about that page («what is this project <URL>») is ordinary chat: the `read_page` tool opens it. Deep site study is only for explicit whole-site requests.
- The router decides the **action time** first: requests about the past («we watched», «you showed earlier») go to history search, while camera/image/video categories only cover actions to perform now.
- Classification sees a small **session micro-context** — the last few real messages plus up to two long-term facts — so «did we look at the camera images?» is answered from history instead of triggering the camera again.
- Tool calls in `none` mode run on the fast worker and stream their progress live.
- There is no hardcoded keyword routing anywhere in the code.

Full routing prompt and stage labels: [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 📋 System Requirements

### Two deployment modes

- **GPU mode (NVIDIA, CUDA 12.2+)** — the recommended mode. The whole stack runs on CUDA builds with the NVIDIA Container Toolkit.
- **CPU-only mode** — the same feature set with no GPU. Everything is slower, so image and video generation automatically downscale to stay inside their time budget.

> ⚠️ **AMD and Intel GPUs are not supported by the official compose stack** — the prebuilt images are CUDA-only. Run CPU-only mode for a guaranteed result.

### Hardware tiers

| Component | Minimal | Moderate | Full | CPU-only |
|-----------|---------|----------|------|----------|
| **GPU VRAM** | 8 GB | 12 GB | 16+ GB | — |
| **RAM (minimum)** | 16 GB | 24 GB | 24 GB | 24 GB |
| **RAM (recommended)** | 24 GB | 32 GB | 32 GB | 48 GB |
| **CPU** | 4+ cores | 6+ cores | 6+ cores | 8+ cores (12 recommended) |
| **Storage** | 60 GB | 80+ GB SSD | 100+ GB SSD NVMe | 100+ GB SSD NVMe |

Only one llama.cpp model is resident at a time, so the RAM budget is the OS plus PostgreSQL/Redis plus the web app, plus CPU-offloaded layers for reasoning on small GPUs. On CPU-only the seed stack is lightweight: a ~11 GB reasoning model and a ~2.5 GB multimodal model. Video generation needs its own headroom — on CPU it dominates, and the planner downgrades the format to fit both RAM and a wall-clock budget.

### What works at each tier

| Feature | 8 GB | 12 GB | 16+ GB | CPU-only |
|---------|------|-------|--------|----------|
| Chat + vision (Qwen3VL) | ✅ 4B (light) | ✅ 8B | ✅ 8B | ✅ 4B (light) |
| Reasoning | ⚠️ partial offload (~15–20 tok/s) | ✅ ~70–90 tok/s | ✅ ~107 tok/s | ✅ MXFP4 model, slow |
| Image generation | ✅ up to 1024×1024 | ✅ up to 1536×1024 | ✅ up to 1536×1024 | ⚠️ resolution halved |
| Image editing | ✅ up to 768 px | ✅ up to 1024 px | ✅ up to 1024 px | ⚠️ resolution halved |
| Video generation | ⚠️ 512×512×120 | ✅ 768×512×240 | ✅ 768×512×240 | ⚠️ adaptive downgrade |
| Voice (Whisper + TTS) | ✅ CPU | ✅ CPU | ✅ CPU | ✅ CPU |
| Document search (Qdrant) | ✅ | ✅ | ✅ | ✅ |
| Long-term memory (SLM) | ✅ CPU | ✅ CPU | ✅ CPU | ✅ CPU |

Measured throughput for the default stack and every candidate model: [BENCHMARKS.md](docs/BENCHMARKS.md).

### Software prerequisites

- Linux server, Docker Engine ≥ 20.10, Docker Compose ≥ 2.0
- **GPU mode:** NVIDIA drivers + NVIDIA Container Toolkit, GPU with 8 GB+ VRAM
- **CPU-only mode:** plain Docker, no NVIDIA tooling
- Internet access once, for model downloads — after that FLAI works fully offline

### CUDA driver compatibility

`deploy.sh` reads the host driver and selects matching images. **Minimum supported driver: CUDA 12.2.**

| Host driver | Images | Notes |
|-------------|--------|-------|
| ≥ 13.0 | CUDA 13.0.1 + `llama-swap:v255-cuda13-b10991` | Standard |
| 12.8 – 12.9 | CUDA 12.8.1 (Ubuntu 24.04) | Standard |
| 12.6 – 12.7 | CUDA 12.6.3 (Ubuntu 24.04) | Image requirements waived |
| 12.4 – 12.5 | CUDA 12.4.1 (Ubuntu 22.04) | Image requirements waived |
| **12.2 – 12.3 (min)** | CUDA 12.2.2 (Ubuntu 22.04) | Image requirements waived for all GPU services |

llama-swap is pinned on purpose: upstream changed its config format in v243, and a floating tag then breaks fresh deployments. Below 12.2 the script refuses GPU mode and falls back to CPU-only.

Deployment problems: [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) · auto-fit details: [VRAM_MANAGEMENT.md](docs/VRAM_MANAGEMENT.md).

## 🚀 Quick Start

> 💡 GPU deployment requires the **NVIDIA drivers** and the **NVIDIA Container Toolkit**.

### Option A — automated (recommended)

One script generates `.env` with secure secrets, downloads the models, builds the images and starts everything.

```bash
git clone https://github.com/barval/flai.git
cd flai

# Core: chat, vision, reasoning, tool calling
./deploy.sh --download-models

# + image generation and editing
./deploy.sh --download-models --with-image-gen

# + voice — pick ONE backend (Piper is the default, --with-voice is an alias)
./deploy.sh --download-models --with-image-gen --with-voice-piper
./deploy.sh --download-models --with-image-gen --with-voice-kokoro

# + document search (Qdrant)
./deploy.sh --download-models --with-image-gen --with-voice-piper --with-rag

# + video generation (LTX-Video)
./deploy.sh --download-models --with-image-gen --with-voice-piper --with-rag --with-video

# + long-term memory, + web search, + site crawler
./deploy.sh --download-models --with-image-gen --with-voice-piper --with-rag \
  --with-video --with-slm --with-search --with-crawler

# Run the test suite after deployment
./deploy.sh --download-models --with-image-gen --run-tests
```

No NVIDIA GPU? Add `--cpu`; `docker-compose.cpu.yml` is then selected automatically. Switching between GPU and CPU needs no rebuild — the images are tagged per backend and coexist locally.

### Option B — manual

```bash
git clone https://github.com/barval/flai.git
cd flai

sudo mkdir -p data data/uploads data/documents
sudo chown -R 1000:1000 data
cp .env.example .env
```

Generate the two secrets and set your timezone, URLs and preferences:

```bash
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")|" .env
sed -i "s|^QDRANT_API_KEY=.*|QDRANT_API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")|" .env
nano .env
```

Download the models into `services/*/models/`. The full list of files, sizes, licenses and `wget` commands is in [MODELS.md](docs/MODELS.md); the Model Hub in the admin panel can fetch them for you with progress and resume.

Start the services — each profile is optional:

```bash
# Chat, vision, reasoning, tools (the base stack)
docker compose -f docker-compose.gpu.yml up -d

# Add subsystems
docker compose -f docker-compose.gpu.yml \
  --profile with-image-gen --profile with-voice-piper --profile with-rag \
  --profile with-video --profile with-slm --profile with-search --profile with-crawler up -d

# CPU-only hosts use the other compose file
docker compose -f docker-compose.cpu.yml up -d
```

> ⏱️ The first build compiles stable-diffusion.cpp from source and takes a while; later builds use the cache.

Set the admin password and open the instance:

```bash
docker exec flai-web flask admin-password YourSecurePassword123
```

Then log in as `admin` at `http://localhost:5000`:

1. **Admin Panel → Models** — confirm the multimodal, reasoning and embedding models; adjust parameters if needed. A context window that would not fit the RAM/VRAM budget is rejected on save, and a background dry-load rolls the configuration back if loading fails.
2. **Admin Panel → Users** — create accounts and assign service classes and camera permissions.
3. **Send your first message** — the router picks the subsystem. Attach a document, an image or a voice message to see the other paths.

### Everything is configured with environment variables

Every service has a profile, and most settings live in `.env`: compose profiles, timeouts, VRAM and memory limits, model URLs, search keys, Tavily and crawler settings, API rate limits, branding. Many runtime settings (models, users, backups, branding, cameras) are managed in the admin panel. The complete reference with defaults is in [CONFIGURATION.md](docs/CONFIGURATION.md).

### You're ready

Chat and reasoning · image analysis · image generation and editing · video generation · voice messages and spoken answers · document search · web search and site reading · deep analysis · long-term memory · camera snapshots · chat export · backups · the `/v1` API.

## 🛠️ Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, the code layout, the test commands and the project's hard rules. In short: fork, branch from the current version branch, keep changes surgical, and open a pull request.

## 🙏 Acknowledgments

- [@Andrey-1](https://github.com/Andrey-1) — extensive testing and valuable feedback
- The llama.cpp, llama-swap, stable-diffusion.cpp, LTX-Video, Qdrant, SearXNG, Tavily, Crawl4AI, Whisper, Piper, Kokoro and SuperLocalMemory projects, and everyone who contributes to them
- Key libraries: Flask, Flask-Babel, Flask-Limiter, Flask-WTF, gunicorn, gevent, requests, python-dotenv, PostgreSQL (psycopg2), Redis, qdrant-client, Pillow, trafilatura, pymorphy3, pendulum, pdfplumber, python-docx, ebooklib, odfpy, striprtf, python-magic, gguf, flasgger (Swagger UI), marked and DOMPurify
- The Qwen, bge-m3, Flux.2 Klein, Z-Image-Turbo, gpt-oss and ruAccent models, hosted on Hugging Face

## 📄 License

MIT License — see [LICENSE](LICENSE).

---

<br>
<div align="center"> Made with ❤️ for the local AI community</div>