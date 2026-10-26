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

- **Everything runs on your hardware.** Chat, reasoning, image and video generation, voice, document search and web search — no cloud service on the processing path (the only exception is optional web search through Tavily).
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
- [🆕 What's New in v12.8](#-whats-new-in-v128)
- [🖼 Demo](#-demo)
- [✨ Features](#-features)
- [🔎 Working with document search](#-working-with-document-search)
- [🏗 Architecture Overview](#-architecture-overview)
- [🧭 Types of Requests & Search Mechanisms](#-types-of-requests--search-mechanisms)
- [📋 System Requirements](#-system-requirements)
- [🚀 Quick Start](#-quick-start)
- [🛠 Contributing](#-contributing)
- [🙏 Acknowledgments](#-acknowledgments)
- [📄 License](#-license)
<!-- TOC:END -->

### 📚 Full documentation

Every document exists in English and Russian (`X.md` / `X-ru.md`).

| Topic | English | Russian |
|-------|---------|---------|
| Architecture, queue, GPU lock, data flow | [ARCHITECTURE.md](docs/ARCHITECTURE.md) | [ARCHITECTURE-ru.md](docs/ARCHITECTURE-ru.md) |
| VRAM accounting, context auto-fit, hardware | [VRAM_MANAGEMENT.md](docs/VRAM_MANAGEMENT.md) | [VRAM_MANAGEMENT-ru.md](docs/VRAM_MANAGEMENT-ru.md) |
| Context window budget and allocation | [CONTEXT.md](docs/CONTEXT.md) | [CONTEXT-ru.md](docs/CONTEXT-ru.md) |
| Measured model throughput | [BENCHMARKS.md](docs/BENCHMARKS.md) | [BENCHMARKS-ru.md](docs/BENCHMARKS-ru.md) |
| Models, download commands, licenses | [MODELS.md](docs/MODELS.md) | [MODELS-ru.md](docs/MODELS-ru.md) |
| Environment variables, compose profiles | [CONFIGURATION.md](docs/CONFIGURATION.md) | [CONFIGURATION-ru.md](docs/CONFIGURATION-ru.md) |
| Web search, Tavily keys, site crawler | [SEARCH.md](docs/SEARCH.md) | [SEARCH-ru.md](docs/SEARCH-ru.md) |
| Deep analysis (RLM) | [DEEP_ANALYSIS.md](docs/DEEP_ANALYSIS.md) | [DEEP_ANALYSIS-ru.md](docs/DEEP_ANALYSIS-ru.md) |
| Background tasks (Tasks) | [TASKS.md](docs/TASKS.md) | [TASKS-ru.md](docs/TASKS-ru.md) |
| Image generation and editing | [IMAGE_GENERATION.md](docs/IMAGE_GENERATION.md) | [IMAGE_GENERATION-ru.md](docs/IMAGE_GENERATION-ru.md) |
| Video generation (LTX-Video) | [VIDEO.md](docs/VIDEO.md) | [VIDEO-ru.md](docs/VIDEO-ru.md) |
| Voice: Whisper, Piper, Kokoro | [VOICE.md](docs/VOICE.md) | [VOICE-ru.md](docs/VOICE-ru.md) |
| Camera integration | [CAMERA.md](docs/CAMERA.md) | [CAMERA-ru.md](docs/CAMERA-ru.md) |
| Long-term memory (SLM) | [MEMORY.md](docs/MEMORY.md) | [MEMORY-ru.md](docs/MEMORY-ru.md) |
| Documents, RAG, PDF OCR | [DOCUMENTS.md](docs/DOCUMENTS.md) | [DOCUMENTS-ru.md](docs/DOCUMENTS-ru.md) |
| RAG quality measurement (hit@k, MRR, nDCG, latency) | [RAG_EVAL.md](docs/RAG_EVAL.md) | [RAG_EVAL-ru.md](docs/RAG_EVAL-ru.md) |
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

## 🆕 What's New in v12.8

Full history: [CHANGELOG.md](CHANGELOG.md).

## 🖼 Demo

Four self-contained pages show the interface without installing anything — download a file
and open it in a browser; no server and no network are needed:

- [Chat (English)](docs/demo/chat-en.html) · [Chat (Russian)](docs/demo/chat-ru.html)
- [Admin panel (English)](docs/demo/admin-en.html) · [Admin panel (Russian)](docs/demo/admin-ru.html)

Everything is fictional except the interface and the data of the machine the demo was built
on; every button answers with a notice instead of doing anything. → [DEMO.md](docs/DEMO.md)

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
- **Document search (RAG)** — PDF, DOC, DOCX, TXT, ODT, RTF, CSV, JSON, EPUB, indexed in Qdrant; every indexed file is guaranteed a slot in the context. **Hybrid search** (vectors + full-text with reciprocal rank fusion, on by default) and an optional **CPU cross-encoder reranker** handle entity questions the vectors miss. → [DOCUMENTS.md](docs/DOCUMENTS.md)
- **Document folders** — single-level folders in the Documents panel: create, rename and delete (deleting a folder also deletes the documents inside); move documents by button or drag-and-drop; select several at once to move or delete (the bar shows the total size); upload straight into a folder. The same checkboxes feed the Deep Analysis corpus when the 🔬 toggle is on. → [DOCUMENTS.md](docs/DOCUMENTS.md)
- **Scanned PDF OCR** — pages with no extractable text are rendered and transcribed by the vision model before indexing.
- **Site reading and deep study** — open one URL or crawl a whole site into a searchable document.

## 🔎 Working with document search

Document search has several layers. Which one earns its cost depends on the question:

| Question | Best tool | Why |
|---|---|---|
| Conceptual or paraphrased | **Vector search** (always on) | matches meaning rather than words and adds no latency |
| Exact names, cities, codes, tool names — or one large file floods the results | **Hybrid search** (on by default) | adds PostgreSQL full-text with Russian stemming and fuses it with the vectors through reciprocal rank fusion; the lexical half only fires when the question carries a rare term |
| The right fragment is found but ranked second or third | **Reranker** (off by default) | a CPU cross-encoder reorders the top candidates; it pays off in narrow windows, loses ranking quality in wide ones and adds ~0.3 s |
| "List all…", "how many…", compare every entry of a document | **Deep analysis** (🔬 with documents selected) | walks the whole corpus step by step instead of fitting one fixed context window |
| Only part of the corpus matters | **Search scope** (🔍) | removes competing documents before retrieval — usually the strongest lever |
| Resumes, contracts, logs and other entry-based files | **Structured chunking** | keeps one logical entry in one chunk, so listing and counting stay possible |

How to use them:

1. **Narrow the scope first** — a folder and/or an upload period removes competing documents before
   retrieval, which buys more than any ranking setting. The scope is shown as a chip and applies to the
   messages that follow; a document attached to a message is always visible, even outside the scope.
2. **Keep hybrid search on** — it helps with names, codes and large files for tens of milliseconds.
   Turning it off makes sense only on a tiny corpus where the lexical half adds nothing.
3. **Enable the reranker selectively** — when the right fragment consistently ranks second or third and
   the context window is narrow (few fragments). In a wide window (a couple of dozen fragments) it
   lowers ranking quality and adds ~0.3 s; keep `Candidates to rerank` at 10.
4. **Choose chunking by document shape** — `structured` for resumes, contracts and logs, `recursive` or
   `fixed` for continuous prose; after changing the strategy, re-index (`flask rag-reindex --user <login>`).
5. **Switch to deep analysis when the answer is a list or a count** over a document: ordinary search
   returns the best fragments, not the whole document.

Every layer is measured with the built-in harness instead of being enabled on faith: `flask rag-eval`
reports hit@k, MRR, nDCG and latency, and the decision log lives in [RAG_EVAL.md](docs/RAG_EVAL.md)
together with the layered design in [DOCUMENTS.md](docs/DOCUMENTS.md).

### 🧠 Memory & context
- **Long-term memory (SLM)** — rule-based, CPU-only fact extraction and merging with semantic deduplication; a profile per user, erased with the account. → [MEMORY.md](docs/MEMORY.md)
- **Context budgeting** — real token costs, not guesses: SLM facts first, then search, then a rolling summary, then history. → [CONTEXT.md](docs/CONTEXT.md)
- **Per-request token counts** — every answer header shows the real output and input tokens. → [ARCHITECTURE.md](docs/ARCHITECTURE.md)

### 🗣️ Voice, camera & web UI
- **Voice messages** — Whisper ASR transcription.
- **Speech synthesis** — Piper (lightweight) or Kokoro (higher quality), selectable at deploy time; male or female voice. → [VOICE.md](docs/VOICE.md)
- **Camera snapshots (optional)** — request a frame from an IP camera and analyse it with the vision model, with per-user permissions. → [CAMERA.md](docs/CAMERA.md)
- **Attachments** — up to 4 images, one document or up to 4 audio items (one voice message plus audio files: 1 voice + 3 files, or 4 files) per chat message. Images appear as thumbnails no taller than the Send button; documents, audio and voice appear as emoji chips. A document is indexed into RAG so the model can answer questions about it; several images are analysed together (compare/group questions); every audio item is transcribed in order and answered. Clipboard paste (Ctrl+V) adds to the queue. → [DOCUMENTS.md](docs/DOCUMENTS.md)
- **Queue visibility** — live position and stage indicators, progress bars, task cancellation, unread markers.
- **HTML blocks** — run generated HTML from a message in a sandboxed preview tab.

### 🔌 API & automation
- **Per-user API keys** — created in the web UI, stored as SHA-256 digests, revocable.
- **OpenAI-compatible `/v1`** — chat completions (sync + SSE), async chat, embeddings, image and video generation, audio speech and transcription, files and documents, RLM, sessions and owner-scoped task polling. Multi-image chat requests, deep-analysis image attachments and image-generation references are accepted (up to 4 images, one joint vision call).
- **Interactive reference** — Swagger UI at `/v1/docs`, OpenAPI document at `/v1/openapi.json`, served offline with bundled assets. → [API.md](docs/API.md)

### ⚙️ Administration
- **Users** — create, edit, delete, service classes, camera permissions, per-user storage limits (enforced on every upload path) and the month-to-date internet-request count.
- **Models** — select and tune GGUF models per module; a context window that would not fit the hardware is rejected.
- **Model Hub** — search Hugging Face for GGUF models, see a VRAM/RAM fit estimate before downloading, download with progress and resume, delete what you no longer need.
- **Backups** — full or per-user backup and restore from the admin panel.
- **Personalization** — your own header logo and localized site names, included in full backups.
- **Monitoring** — hardware and database overview plus a usage tab: storage, sessions, messages, tasks, tokens, files, documents and internet requests per user over 1 h – 30 d. → [MONITORING.md](docs/MONITORING.md)
- **CLI tools** — admin password, upload cleanup, message-format migration, SLM import and cleanup. → [DEVELOPMENT.md](docs/DEVELOPMENT.md)

### 🔒 Privacy & security
- 100% local processing; nothing leaves your network except the search services you configure.
- Session auth with password hashing, HttpOnly/SameSite cookies, CSRF on every form, login rate limiting and audit logging.
- Strict per-user isolation of sessions, messages, documents and camera access.
- Redis queue tasks are HMAC-signed; file paths are validated for traversal; all markdown HTML is sanitized with DOMPurify.
- Every URL that leaves the app passes an SSRF guard (no private, loopback, link-local or CGNAT addresses).

## 🏗 Architecture Overview

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
| **RAM (minimum)** | 16 GB | 24 GB | 24 GB | 16 GB (24 GB with media) |
| **RAM (recommended)** | 24 GB | 32 GB | 32 GB | 48 GB |
| **CPU** | 4+ cores | 6+ cores | 6+ cores | 8+ cores (12 recommended) |
| **Storage** | 60 GB | 80+ GB SSD | 100+ GB SSD NVMe | 100+ GB SSD NVMe |

**Optional components add to this budget.** The CPU reranker (`bge-reranker-v2-m3`, off by default) costs
~0.42 GB of disk and holds about **0.9 GB of RAM** while resident: it runs in llama-swap's `cpu_only` group,
never touches VRAM and never takes a GPU queue slot, but on the CPU-only tier it competes with the reasoning
model for cores (a warm query costs ~150 ms, the first one ~1.5 s). Hybrid search adds a PostgreSQL mirror of
the chunks — a few MB per thousand chunks, no GPU memory.

Only one llama.cpp model is resident at a time, so the RAM budget covers the OS, PostgreSQL/Redis, the web app and the layers offloaded to CPU for reasoning on small GPUs. On CPU-only the seed stack is lightweight: a ~11 GB reasoning model and a ~2.5 GB multimodal model. Video generation needs its own headroom — on CPU it dominates, and the planner downgrades the format to fit both RAM and a wall-clock budget.

### What works at each tier

| Feature | 8 GB | 12 GB | 16+ GB | CPU-only |
|---------|------|-------|--------|----------|
| Chat + vision (Qwen3VL) | ✅ 4B (light) | ✅ 8B | ✅ 8B | ✅ 4B (light) |
| Reasoning | ⚠️ partial CPU offload (~15–20 tok/s) | ✅ ~70–90 tok/s | ✅ ~107 tok/s | ✅ MXFP4 model, slow |
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
- Internet access is needed once, for model downloads — after that FLAI works fully offline

### CUDA driver compatibility

`deploy.sh` reads the host driver and selects matching images. **Minimum supported CUDA version: 12.2 (driver 525.60.13+).**

| CUDA version (driver branch) | Images | Notes |
|------------------------------|--------|-------|
| ≥ 13.0 | CUDA 13.0.1 + `llama-swap:v255-cuda13-b10991` | Standard |
| 12.8 – 12.9 | CUDA 12.8.1 (Ubuntu 24.04) | Standard |
| 12.6 – 12.7 | CUDA 12.6.3 (Ubuntu 24.04) | NVIDIA image requirements disabled (`NVIDIA_DISABLE_REQUIRE=1`) |
| 12.4 – 12.5 | CUDA 12.4.1 (Ubuntu 22.04) | NVIDIA image requirements disabled (`NVIDIA_DISABLE_REQUIRE=1`) |
| **12.2 – 12.3 (min)** | CUDA 12.2.2 (Ubuntu 22.04) | NVIDIA image requirements disabled for all GPU services (`NVIDIA_DISABLE_REQUIRE=1`) |

llama-swap is pinned on purpose: it changed its config format in v243, and a floating tag then breaks fresh deployments. Below 12.2 the script refuses GPU mode and falls back to CPU-only.

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

1. **Admin Panel → Models** — confirm the multimodal, reasoning and embedding models; adjust parameters if needed. A context window that would not fit the RAM/VRAM budget is rejected on save, and a background test load (dry-load) rolls the configuration back if loading fails.
2. **Admin Panel → Users** — create accounts and assign service classes and camera permissions.
3. **Send your first message** — the router picks the subsystem. Attach a document, an image or a voice message to see the other paths.

### Everything is configured with environment variables

Every service has a profile, and most settings live in `.env`: compose profiles, timeouts, VRAM and memory limits, model URLs, search keys, Tavily and crawler settings, API rate limits, branding. Many runtime settings (models, users, backups, branding, cameras) are managed in the admin panel. The complete reference with defaults is in [CONFIGURATION.md](docs/CONFIGURATION.md).

### You're ready

Chat and reasoning · image analysis · image generation and editing · video generation · voice messages and spoken answers · document search · web search and site reading · deep analysis · long-term memory · camera snapshots · chat export · backups · the `/v1` API.

## 🛠 Contributing

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