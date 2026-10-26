# Dependency and platform update audit

Audit date: **2026-10-26**. This document records what is out of date in the shipped stack, which
updates carry security fixes, which upstream features are worth adopting, and how the upgrades
should be sequenced. It is an input to the `features/v12.8-dependency-updates` branch, not a
commitment to update everything at once.

## Purpose

FLAI pins a small number of components and floats the rest through version ranges. This audit
answers three questions for one release cycle:

1. Which components have security fixes that cannot wait?
2. Which updates are safe mechanical bumps and which need a compatibility check?
3. Which new upstream capabilities are worth adopting in the product itself?

## How the audit was made

- Installed versions: `pip list --outdated` in the development virtualenv, `pip freeze` inside the
  running `flai-web` container, `docker inspect` and the version commands of the running services.
- Published versions: PyPI JSON, Docker Hub and GHCR tag APIs, GitHub releases, `endoflife.date`
  for Python.
- Vulnerabilities: the OSV database (`/v1/querybatch`) for the installed Python package versions,
  plus the upstream security notes of Redis and Crawl4AI.
- Code impact: `app/platform_detect.py`, `app/resource_manager.py`, `app/llama_swap_config.py`,
  `deploy.sh`, `docker-compose.gpu.yml` and the service Dockerfiles.

The numbers below are a snapshot. Refresh them with the commands listed under
[Keeping this document current](#keeping-this-document-current).

## Runtime and images

| Component | Shipped | Latest available | Note |
|---|---|---|---|
| Python in the image | 3.11.16 (`python:3.11-slim`) | 3.11.18 | 3.11 is in security-only support until 2027-10-31 |
| Python in the dev virtualenv | 3.13.5 | 3.13.18 | 3.14.10 and 3.15.2 are also released |
| PostgreSQL | 18.4 | 18.6 | Minor bug fixes only; `postgres:18-alpine` floats |
| Redis | 8.4.2 | 8.4.8 (security) | Newer lines exist (8.6, 8.8, 8.10); a line jump needs a queue test |
| Qdrant | 1.19.0 | 1.20.0 | The Python client must move to 1.20.0 as well |
| llama-swap | v255 | v267 | Released on the audit date; GHCR images had not been pushed yet (the newest published line in the registry was v260) |
| llama.cpp | b10991 | b11898 | About 900 builds behind; bundled into the llama-swap image tag |
| Whisper ASR | v1.9.1 | v1.10.0 | New base image with CUDA 12.9.2 |
| Crawl4AI | 0.9.4 | 0.9.5 | Security release, see below |
| SearXNG | floating `latest`, image built 2026-09-17 | 2026.10.25 | The floating tag is not reproducible; pin a dated tag |
| stable-diffusion.cpp | built from `master` | — | Not reproducible; pin a commit |
| LTX-Video base | PyTorch 2.5.0, CUDA 12.4 | PyTorch 2.14.1 | A major jump; the LTX wrapper code must be checked |
| Kubernetes-side services | — | — | Not applicable; the project is Compose-only |

## Security findings

| Package / image | Installed | Fixed in | Advisory | Impact for FLAI |
|---|---|---|---|---|
| Pillow | 12.3.0 | 13.0.0 | CVE-2026-112390 (MIC infinite loop), CVE-2026-112396 (TIFF heap use-after-free), CVE-2026-112899 (EPS/Ghostscript limit bypass) and dozens more in the October batch | User-uploaded images, document scans and PDF page renders go through Pillow — highest priority |
| urllib3 | 2.7.0 (the container already has 2.8.0) | 2.8.0 | CVE-2026-97687 (HTTPS proxy TLS settings ignored), CVE-2026-97688 (deflate infinite loop), CVE-2026-97689 (unbounded chunk-size line) | Every outbound HTTP call, including search and crawler requests |
| Werkzeug | 3.1.8 (the container already has 3.1.9) | 3.1.9 | CVE-2026-102598 (`safe_join()` and Windows device names) | Low on Linux, but the patch is trivial |
| python-dotenv | 1.0.0 (pinned) | 1.2.2 | CVE-2026-28684 (symlink following in `set_key()`) | Low attack surface; the unpin is overdue anyway |
| Crawl4AI | 0.9.4 | 0.9.5 | Six coordinated advisories: SSRF in the cache validator, page JavaScript injection through `css_selector` / `wait_for`, ReDoS through caller regexes, unbounded delay fields, Chromium launched with certificate checks off, caller-chosen Hugging Face model | The sidecar parses untrusted pages — upgrade even though FLAI sends its own requests |
| Redis | 8.4.2 | 8.4.8 | Heap buffer overflow when a `PUBLISH`/`SPUBLISH` larger than 4 GB crosses the cluster bus | FLAI runs standalone, so it is not reachable; patch for hygiene |
| PostgreSQL | 18.4 | 18.6 | Bug fixes, no advisory in the release notes | Routine |

The development virtualenv and the running container have drifted: the container already has
Werkzeug 3.1.9, urllib3 2.8.0, cryptography 50.0.2 and gevent 26.9.0, while the virtualenv has
3.1.8, 2.7.0, 50.0.1 and 26.8.0. Without a lock file the two environments will keep diverging.

## Application dependencies

`pip list --outdated` reports **54 outdated packages**. The meaningful ones:

| Package | Installed | Latest | Note |
|---|---|---|---|
| gunicorn | 26.1.0 | 26.2.0 | Pinned in `pyproject.toml` |
| gevent | 26.8.0 | 26.9.0 | The container already has 26.9.0 |
| cryptography | 50.0.1 | 50.0.2 | Patch |
| qdrant-client | 1.19.0 | 1.20.0 | Must match the server upgrade |
| trafilatura | 2.2.0 | 2.3.1 | Parser update; run the extraction tests |
| python-docx | 1.1.0 | 1.2.0 | Pinned; document import path |
| psycopg2-binary | 2.9.12 | 2.9.13 | Patch |
| pypdfium2 | 5.13.0 | 5.14.0 | PDF rendering |
| pytz / pendulum | 2026.3.post1 / 3.2.0 | 2026.5 / 3.3.0 | Time zone and date handling |
| numpy / pydantic | 2.5.3 / 2.13.5 | 2.5.4 / 2.14.1 | Transitive but widely used |
| ruff / mypy | 0.15.12 / 2.1.0 | 0.17.2 / 2.4.0 | Dev tools; expect new lint findings |

## Capabilities worth evaluating

**llama.cpp (b11898)**

- `--spec-type ngram-simple|ngram-map-k|ngram-map-k4v|ngram-mod|ngram-cache`: speculative decoding
  without a draft model. Promising on CPU and on AMD/Intel cards where a draft model would not fit.
- `--reasoning-budget-message`: text injected before the end-of-thinking tag when the budget runs
  out — a cleaner ending than the current silent stop.
- `--cache-reuse N`: KV shifting for repeated prompt prefixes (the chat system prompt).
- `--metrics`: a Prometheus endpoint on llama-server; could feed the monitoring tab.
- `/v1/responses`, `--models-dir` (router server): not needed, llama-swap already owns this role.
- The per-request `reasoning_budget` field is still not documented in the server API, so the
  project's CLI workaround (`--reasoning-budget max(1024, ctx*0.4)`) must stay and be re-tested.

**llama-swap (v260+ / v267)**

- Per-backend images: `cuda`, `cuda13`, `rocm`, `vulkan`, `intel` (SYCL), `musa`, `cpu`, plus
  `-non-root` variants. This removes the "build llama.cpp images ourselves" blocker for AMD and
  Intel.
- The capacity router (v267) is not needed: FLAI serialises GPU work on purpose.
- The non-root images are a cheap security improvement for the model container.

**Qdrant 1.20**

- BM25 scoring directly on the full-text payload index. The hybrid search currently fuses Qdrant
  dense results with PostgreSQL full-text search; native BM25 could remove PostgreSQL from the hot
  path. Measure with the harness from `docs/RAG_EVAL.md`: PostgreSQL uses Russian stemming, while
  BM25 only tokenises, so recall on Russian text may drop.
- `turbo8` / `turbo16` vector types: memory savings for embeddings at some recall cost.
- Per-element payload indexes: faster `doc_id` and metadata filters.
- Audit log to stdout/stderr: easier in containers.

**Other components**

- Crawl4AI 0.9.5 added server-side deep crawling; FLAI deliberately keeps the BFS in the client for
  SSRF control, so this is not adopted.
- Whisper 1.10.0 brings a newer CUDA base and the current whisper/faster-whisper.
- Python 3.13 adds performance and better diagnostics; 3.14 adds t-strings and deferred annotations.
  For FLAI the benefit is modest and dependency support (gevent, psycopg2, qdrant-client) decides
  the timing; 3.13 is the realistic next baseline.
- Redis 8.6+ brings no capability FLAI needs; the reason to move is the security patch.

## Multi-platform status

The roadmap item **Multi-platform GPU support** is partially implemented:

- `app/platform_detect.py` detects `nvidia | amd | intel | cpu`, parses AMD memory through
  `rocm-smi`, reads the Intel GPU name through `vulkaninfo`, and honours the `FLAI_PLATFORM`
  override. The admin Hardware tab shows the result.
- `services/sd_cpp/Dockerfile.sd_cpp` accepts `SD_BACKEND=cuda|vulkan|cpu`, and the SD wrapper is
  backend-aware.
- Image generation and video generation degrade to CPU when CUDA is absent.

Still missing:

- `cuda_detected` is `False` for AMD and Intel, so the resource manager plans them as CPU hosts and
  sets `n_gpu_layers=0` even when `rocm-smi` reported memory.
- `docker-compose.gpu.yml` and `deploy.sh` are NVIDIA-only: `runtime: nvidia`,
  `NVIDIA_VISIBLE_DEVICES`, `SD_BACKEND=cuda`, `LTX_BACKEND=cuda`, no per-platform profiles.
- LTX-Video ships a CUDA-only base image; ROCm needs a torch-ROCm base and a test run.
- Whisper has no ROCm image.
- Apple cannot use Metal inside Linux containers: macOS needs a separate native install path
  (llama.cpp Metal, stable-diffusion.cpp Metal, whisper.cpp, CPU services).

What changed upstream: llama-swap now publishes ROCm, Vulkan and Intel images for every release,
and llama.cpp's Vulkan, ROCm, SYCL and Metal backends are mature. The llama.cpp and SD halves of
the roadmap are therefore close; LTX-Video and the deployment layer are the real work.

## Phase 0 baseline (recorded 2026-10-26)

Measured on the reference machine (RTX 5060 Ti, 16 GB VRAM) before any update:

| Measurement | Value |
|---|---|
| Retrieval, 30 generated questions over 29 real documents, `top_k=20`, coverage on | hit@20 **0.900**, recall@20 **0.900**, MRR **0.688**, nDCG@20 **0.765** |
| Retrieval latency | p50 **39 ms**, p95 **63 ms**; 8123 chars of context per query |
| Chat throughput, median over the last 30 days | reasoning **43.4 tok/s** (n=36, 82.6 s per answer), multimodal **28.4 tok/s** (n=12, 3.8 s) |
| Model set | reasoning `Qwen3.6-35B-A3B-UD-Q2_K_XL` ctx 24576, multimodal `Qwen3VL-8B-Instruct-Q4_K_M` ctx 32768, embedding `bge-m3-Q8_0`, reranker `bge-reranker-v2-m3-Q4_K_M` ctx 2048 |
| Idle VRAM with every model evicted | 527 MiB |
| Backups taken first | `pg_dump` of the database (14 MB) and a `tar.gz` of the `qdrant_data` volume (24 MB), stored outside the repository |

Reproduce the retrieval numbers:

```bash
docker exec flai-web flask rag-eval-generate --user <login> --count 30 --out /app/data/rag_eval/dataset.jsonl
docker exec flai-web flask rag-eval --user <login> --dataset /app/data/rag_eval/dataset.jsonl \
  --top-k 20 --coverage --label "baseline" --out /app/data/rag_eval/baseline.json
```

The Phase 2 gate compares its own `rag-eval` run against this table.

## Phase 0 and Phase 1 result (v12.8)

Shipped on 2026-10-26:

| Change | Detail |
|---|---|
| Python packages | Pillow 12.3.0 → **13.0.0**, python-dotenv 1.0.0 → **1.2.4**, urllib3 2.7.0 → **2.8.0**, Werkzeug 3.1.8 → **3.1.9** |
| Lock file | `requirements.lock` (88 pinned packages) is what the image installs; `requirements.txt` stays the human-edited manifest |
| Images | PostgreSQL 18.4 → **18.6**, Crawl4AI 0.9.4 → **0.9.5**, Redis 8.4.2 → **8.4.7** (8.4.8 has no published image), `python:3.11-slim` and `searxng` pinned by digest |
| CI | `pip-audit` audits the locked set; Trivy scans the three pinned images for HIGH/CRITICAL findings that have a fix |

Gate, run against the live instance after the upgrade:

- image upload → multimodal answer: **pass** (a 2400×1600 upload is downscaled and answered);
- PDF page render: **pass** (990×1400 JPEG through `pdftoppm` and Pillow 13);
- document import and retrieval: **pass** (a fresh note is indexed and its secret code answered);
- web search through SearXNG: **pass**;
- Crawl4AI sidecar: **pass** (`/md` returns markdown for `https://example.com`);
- queue run: **pass** (chat, document indexing and a 1024×1024 image generated by `z_image_turbo`).

Corrected during the work: the audit listed Redis **8.4.8** as the image to use, but Docker Hub
publishes only up to **8.4.7** in the 8.4 line, so the image stops there; the 8.4.8 source fix arrives
with the next published image.

### Phase 2, first half (v12.8)

| Change | Detail |
|---|---|
| Qdrant | server and client 1.19.0 → **1.20.0** |
| gunicorn | 26.1.0 → **26.2.0** |
| python-docx | 1.1.0 → **1.2.0** |

The Qdrant volume was backed up before the first start. The collections came up green with every
point in place (`user_valery` 350 points) and no migration error in the log, and the RAG evaluation
after the upgrade reproduces the Phase 0 baseline exactly: hit@20 0.900, recall@20 0.900, MRR 0.688,
nDCG@20 0.765, p50 39 ms and p95 63 ms once the server is warm (the first run after the restart
showed p95 359 ms while the index was cold). A DOCX built with python-docx 1.2.0 imports, indexes
and previews correctly, and the web service answers under gunicorn 26.2.0.

Because the image installs `requirements.lock`, a version bump is resolved from `requirements.txt` in
a scratch container and then frozen into the lock — see `docs/DEVELOPMENT.md`.

## Proposed update sequence

The phases are grouped into three releases: **v12.8** takes Phase 0 and Phase 1, **v12.9**
takes Phase 2 and Phase 3, and **v13.0** takes Phase 4.

**Phase 0 — baseline (no code changes, v12.8).** Back up PostgreSQL and the Qdrant volume. Record a RAG
evaluation baseline, current throughput figures and a model dry-load log. Add `pip-audit` and a
container scanner to CI.

**Phase 1 — security (v12.8).** Pillow 13, python-dotenv 1.2.4, urllib3 2.8, Werkzeug 3.1.9, Redis 8.4.8,
PostgreSQL 18.6, Crawl4AI 0.9.5. Add a lock file and pin the floating image tags. Gate: image
upload and resize, PDF page render, document import, search and crawl, a full queue run.

**Phase 2 — runtime (v12.9).** Qdrant server and client 1.20.0, gunicorn 26.2.0, gevent 26.9.0,
trafilatura 2.3.1, python-docx 1.2.0, psycopg2 2.9.13. Then llama-swap v267 (or v260 while the v267
images are missing) with llama.cpp b11898. Gate: reasoning with the thinking budget, MTP draft,
multimodal, tool calls, tasks, `/v1` API, RAG evaluation against the Phase 0 baseline.

**Phase 3 — capabilities (v12.9).** Benchmark Qdrant BM25 against the PostgreSQL hybrid; try ngram
speculation and `--cache-reuse`; wire `--metrics`; evaluate `--reasoning-budget-message`.

**Phase 4 — platforms (v13.0).** Compose profiles and `deploy.sh` detection for AMD/Intel, VRAM accounting
for non-NVIDIA GPUs, SD Vulkan, LTX ROCm. macOS stays a separate research task (native install).

## Risks

- **llama.cpp b10991 → b11898** touches chat templates, tool-call parsing, reasoning handling and
  multimodal projectors. The reasoning-budget workaround and the 502 retry logic must be re-tested.
- **Qdrant 1.20** may migrate on-disk data; a volume backup is mandatory before the first start.
- **Pillow 13** is a major release with removed APIs; image generation, editing, OCR and preview
  paths must all be exercised.
- **LTX-Video on a newer PyTorch** can break model loading; treat it as a separate change.
- **No lock file** means "latest at build time"; a lock plus dated image tags is part of the fix,
  not an optional nicety.

## Reproducibility

- `pyproject.toml` uses ranges for almost every dependency; the same commit can produce different
  environments. Introduce a lock file and install from it in the image.
- `postgres:18-alpine`, `searxng/searxng:latest` and `python:3.11-slim` float. Pin exact versions
  or digests.
- stable-diffusion.cpp is cloned from `master` at build time. Pin a commit and record it in the
  image label.

## Keeping this document current

```bash
# Installed vs published
pip list --outdated
docker exec flai-web pip list --outdated

# Known vulnerabilities
pip-audit
# or, without installing anything, query OSV for the installed versions
```

Refresh the tables, bump the audit date, and keep the Russian twin (`UPGRADES-ru.md`) in sync.
