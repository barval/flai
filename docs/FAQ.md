# Frequently Asked Questions

Short answers to the questions that come up most often. For depth, follow the links.

## General

### What is FLAI?

A self-hosted, fully local multimodal AI assistant. It runs chat, reasoning, web search, document search, image generation and editing, video generation, voice transcription and speech synthesis, long-term memory, and camera access — on your own hardware, with no cloud service in the path. See [../README.md](../README.md).

### Does anything leave my machine?

No. All inference runs on your GPU or CPU, and all data stays in your PostgreSQL database and uploads directory. The only outbound traffic is to services *you* configure: the web search engine, and the web pages being fetched when you ask FLAI to search or crawl. Every user-facing URL passes an SSRF guard that rejects private and link-local addresses.

### What license is it?

MIT. See [../LICENSE](../LICENSE).

### Is there a hosted version?

No. FLAI is self-hosted only — that is the point of the project.

---

## Hardware

### Do I need an NVIDIA GPU?

No, but it is strongly recommended. FLAI has two deployment modes with the same feature set:

- **GPU mode (NVIDIA, CUDA 12.2+)** — full speed. Recommended.
- **CPU-only** — everything works, 4–6× slower for media generation. Image and video generation automatically downscale to stay within a wall-clock budget.

On an NVIDIA GPU, 8 GB is the minimum. See [System Requirements](../README.md#-system-requirements) and [BENCHMARKS.md](BENCHMARKS.md).

### What can I do with 8 GB of VRAM?

Chat, tool calling, reasoning, web search, document search and image generation all fit. Video generation is tight on 8 GB, and large reasoning models will have their context window auto-fitted down. The hardware tier table in the README lists what fits at each size.

### Can I run it on AMD or Intel?

Not yet. Multi-platform GPU support is in progress — see [ROADMAP.md](ROADMAP.md). CPU-only mode works everywhere a GPU does not.

### Why did my context window change after I restarted?

Context windows are auto-fitted at deployment from the GGUF metadata on disk plus measured RAM/VRAM. They are deliberately not hand-configured, so swapping a model or adding VRAM changes them. See [CONTEXT.md](CONTEXT.md) and [VRAM_MANAGEMENT.md](VRAM_MANAGEMENT.md).

---

## Setup

### Do I need an API key to use FLAI?

No. FLAI is fully functional with zero external accounts. A Tavily key is an optional quality upgrade for web search — without one, search runs on the local SearXNG engine. See [SEARCH.md](SEARCH.md).

### Where do model files go?

In the `models/` directory (mounted from `./models` in `docker-compose.gpu.yml`). The seed configuration ships without model files. Download commands, sizes and licenses are in [MODELS.md](MODELS.md); `deploy.sh` can fetch them for you.

### Can I add models without restarting?

Yes. The admin panel has a **Model Hub** that searches Hugging Face for GGUF models, shows a VRAM/RAM fit estimate before you download, downloads with progress and resume, and deletes what you no longer need. See [MODELS.md](MODELS.md).

### Which compose profiles do I need?

`--profile with-image-gen` for images, `--profile with-video` for video, `--profile with-rag` for document search, `--profile with-voice-piper` or `--profile with-voice-kokoro` for voice, `--profile with-slm` for long-term memory, `--profile with-search` for web search, `--profile with-crawler` for site crawling. Each is optional; the base stack is chat. See [CONFIGURATION.md](CONFIGURATION.md).

### How do I become admin?

The first account created by `init_db()` is the administrator (`admin`, with `service_class=0`). Set its password:

```bash
docker exec flai-web flask admin-password <password>
```

---

## Using it

### How does FLAI decide what to do with my message?

An LLM router classifies every message into one of eleven categories — document search, web search, history search, offline reasoning, reasoning with fresh web data, deep site study, camera snapshot, remember-a-fact, image generation, video generation, or ordinary chat with tools. There are no keyword rules. The full table is in the README.

### Why didn't it search the web when I asked about a current topic?

Some questions are deliberately kept offline — code, writing, derivations. Analysis of current events routes to web search instead. If the router classified your message as ordinary chat, rephrase: naming that you want the *latest* information, or asking it to look something up, usually flips the decision.

### I pasted a link and asked what the project is. Why no search?

A single link with a question about that page is ordinary chat by design — the `read_page` tool opens the page directly instead of running a full search or crawl. Deep site study (`[-CRAWL-]`) is for explicit whole-site requests. See [SEARCH.md](SEARCH.md).

### What is Deep Analysis?

A reasoning mode for serious work across multiple documents: comparing contracts, extracting every exception from a policy, building a report over a set of texts. The model works through the material in a loop rather than answering from a single retrieval pass. See [DEEP_ANALYSIS.md](DEEP_ANALYSIS.md).

### Can I stop a running task?

Yes — press **Cancel**. Cancellation is supported for image generation and editing, video generation (which also restarts the video container), and streaming chat tasks.

### Why does chat stall while a video is generating?

By design. GPU tasks run strictly one at a time, and VRAM is cleaned unconditionally between them, so chat waits for the video task to finish. See [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Data, privacy and users

### Where is my data?

In PostgreSQL (messages, sessions, users, documents metadata) and in bind-mounted directories (`./data`, `./uploads`, `./models`). Full backups are available from the admin panel. See [ADMINISTRATION.md](ADMINISTRATION.md).

### What happens to my data if I delete my account?

Everything. Deleting a user erases their messages, sessions, uploaded documents, SLM memory profile and API keys. It is a GDPR erasure path, not a soft delete. See [ADMINISTRATION.md](ADMINISTRATION.md).

### Can several people share one instance?

Yes, with one caveat: FLAI holds a single GPU queue, so heavy media generation by one user delays everyone. For normal chat and search use, multiple users share an instance comfortably. The router uses session history and long-term facts to disambiguate "you", but sessions are per user.

### Can I use FLAI from my own application?

Yes — a public OpenAI-compatible API. `/v1/chat/completions`, `/v1/embeddings`, image and video generation, audio transcription and speech, file uploads, sessions and task control. Keys are per user. Interactive reference at `/v1/docs`. See [API.md](API.md).

### Is the API rate-limited?

Yes: 60 requests per minute and 1000 per hour, counted per API key owner (all of one user's keys share one budget). See [API.md](API.md).

---

## Operations

### How do I back up?

Admin panel → Backups, or the CLI. A full backup includes the database, uploads, and branding. See [ADMINISTRATION.md](ADMINISTRATION.md).

### How do I monitor it?

`/health` returns a JSON status; `/metrics` exposes Prometheus metrics including `flai_web_info`. The admin panel has a monitoring tab and a hardware overview. See [MONITORING.md](MONITORING.md).

### Something is broken

Start with [TROUBLESHOOTING.md](TROUBLESHOOTING.md). If your problem is not there, check [../CHANGELOG.md](../CHANGELOG.md) for a known regression.