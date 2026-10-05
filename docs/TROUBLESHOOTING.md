# Troubleshooting

Common deployment and runtime problems. If you hit something not listed here, check `docker logs` on the affected container first, then [../CHANGELOG.md](../CHANGELOG.md) for whether it is a known regression.

Every user-facing FLAI error starts with `⚠️ `. Messages in the chat that do not are coming from the browser or a proxy, not from FLAI.

## First three commands

```bash
docker compose ps                      # which containers are up and healthy
docker logs flai-web --tail 100        # the web app
curl -s localhost:5000/health | python3 -m json.tool
```

`/health` reports database connectivity, Redis, Qdrant and the loaded model state. It is the fastest way to tell "FLAI is down" from "one subsystem is down".

---

## Deployment problems

### `detect_cuda` says the driver is below the minimum

```
CUDA driver 12.1 is below the minimum supported version (CUDA 12.2)
```

FLAI needs a host driver from **CUDA 12.2** up (driver 525.60.13+). The deploy script accepts 12.2 as a non-standard deployment — NVIDIA image requirements are waived for llama-swap, LTX-Video and sd.cpp because CUDA 12.x minor-version compatibility keeps them working (verified by users on an RTX 3090 + CUDA 12.2).

Below 12.2 the script refuses GPU mode and falls back to CPU-only. To fix, update the driver — do not force GPU mode on an unsupported driver.

### Containers fail with `could not select device driver "" with capabilities: [[gpu]]`

The NVIDIA Container Toolkit is missing or not wired into Docker. Check it:

```bash
nvidia-smi                 # driver works on the host?
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

If the second command fails, install the NVIDIA Container Toolkit for your Docker version and restart the Docker daemon. This is a host-level problem — no FLAI setting will work around it.

### `port is already allocated`

Port 5000 is taken by another process:

```bash
ss -ltnp | grep 5000
```

Change the published port in `docker-compose.gpu.yml` (`"5000:5000"` → `"8080:5000"`) or stop whatever holds 5000.

### The crawler container exits immediately

The Crawl4AI sidecar binds loopback only unless a token is set, so the web container cannot reach it without one:

```bash
grep CRAWL4AI_API_TOKEN .env        # must be non-empty and match CRAWL_API_TOKEN in the web service
docker logs flai-crawler --tail 50
```

Without the token the sidecar stays bound to `127.0.0.1` inside the container network and every request fails. See [SEARCH.md](SEARCH.md).

---

## Models and VRAM

### A GPU task hangs and then reports a VRAM timeout

FLAI serializes GPU work behind a single lock and waits for VRAM to be released before loading the next model. A wait that never finishes means something is holding memory:

```bash
nvidia-smi                                   # who is using VRAM right now
curl -s localhost:5000/health | python3 -m json.tool
docker logs flai-web --tail 200 | grep -i vram
```

Two causes account for most cases:

1. **A model was respawned externally** while FLAI was waiting. The wait loop is self-healing — it re-unloads a model that reappears in llama-swap's `/running` on each poll — but a second process on the host (a manual llama-server, another container) will keep fighting it. Stop the other process.
2. **LTX-Video is stuck.** Three consecutive timeouts make FLAI restart the video container. Check `docker logs flai-ltxvideo`.

The health-monitor watchdog deliberately skips its whole tick while a GPU task holds the lock, so a repeated model spawn in the log is a symptom, not the cause.

### `compute_llamacpp_config` reduces n_gpu_layers to 0 and still fails

The model does not fit in VRAM at all. FLAI degrades before loading rather than failing with OOM, so this means the model is larger than the available VRAM even with all layers on CPU. Either pick a smaller quantization, or let the auto-fit lower the context window — see [VRAM_MANAGEMENT.md](VRAM_MANAGEMENT.md) and [BENCHMARKS.md](BENCHMARKS.md).

### Generation dies with «Image generation timeout» around step 3/10

That was the old CPU path: full resolution on a CPU does not finish inside `SD_CPP_TIMEOUT`. On CPU, FLAI halves both sides of the resolution (~4× fewer pixels) and notifies you via the `resize_notice` channel. If it still times out, raise `SD_CPP_TIMEOUT` for your CPU container.

### Video generation is killed on CPU

CPU video is 4–6× slower than GPU. The planner picks the largest format that fits both available RAM and the wall-clock budget (`LTX_VIDEO_CPU_TIME_BUDGET_S`, default 85% of `LTX_VIDEO_TIMEOUT`) and tells you the format it chose. If even the smallest step does not fit, it stops with an explicit message instead of hanging — check the numbers before retrying.

### LTX-Video needs a newer driver

On CUDA 12.2 the deploy script sets `LTX_DISABLE_REQUIRE=1`, which waives the image requirement but does not guarantee video works. If video generation fails on an old-but-supported driver, this is why — upgrade the driver.

---

## Subsystems that are not reachable

### Web search returns «Search services are temporarily unavailable»

SearXNG is down or unreachable:

```bash
docker ps -a --filter name=flai-searxng
docker logs flai-searxng --tail 50
curl -s "localhost:8080/search?q=test&format=json" | head -c 300
```

FLAI retries once on an empty result set, and only then shows this soft notice. Note that some engines answer with zero results without failing — the `0 results` diagnostic in the log lists engines that responded versus engines that did not, which distinguishes the two cases.

### Tavily shows as unavailable in the profile popup

The key is per user and must be valid on Tavily's side. `GET /api-keys/tavily` returns `status` as `ok`, `unavailable`, `exhausted`, `invalid` or `no_key`. `exhausted` means the monthly credit is spent — search silently falls back to SearXNG, which is free. See [SEARCH.md](SEARCH.md).

### Long-term memory is not remembering anything

```bash
grep -E "^SLM_URL" .env                 # commented out means memory is off
docker inspect --format '{{.State.Health.Status}}' flai-slm
```

Note that `docker-compose.gpu.yml` configures the SLM container with `SLM_PORT=8765` while its healthcheck polls **8766**. If the healthcheck reports unhealthy, that mismatch is why — check the container's own logs before suspecting FLAI. See [MEMORY.md](MEMORY.md).

### Document search returns nothing

```bash
docker inspect --format '{{.State.Health.Status}}' flai-qdrant
```

Confirm the Qdrant profile is active and that the document was actually indexed — indexing failures are reported per document in the Documents panel, not silently.

### A file type is rejected on upload

FLAI accepts PDF, DOC, DOCX, TXT, ODT, RTF, CSV, JSON and EPUB, subject to `MAX_DOCUMENT_SIZE_MB` and the per-user document quota. Image uploads are resized and converted before storage. See [DOCUMENTS.md](DOCUMENTS.md).

---

## Slowness and resource use

### The host runs out of memory during tests

A pytest run builds a fresh Flask app per test, so a suite holds hundreds of app instances over its lifetime. **Never run two pytest processes at once**, and check that a finished run has actually exited before starting the next:

```bash
free -h
pgrep -c pytest         # must be 0 before the next run
```

Stop the run if available RAM drops below ~4 GiB or swap is more than half used. Prefer targeted runs (`pytest -m unit`) plus the suites covering what you changed.

### The web container restarts repeatedly

```bash
docker inspect --format '{{.RestartCount}} {{.State.Health.Status}}' flai-web
docker logs flai-web --tail 200
```

A crash loop usually means a configuration error rather than a code bug. The most common cause is a malformed `.env` value. `FLM_SESSION_LIFETIME_SECONDS` and similar lifetime settings are fixed in code and have no env override — setting them does nothing.

### Requests feel slow but no model is loaded

Check what the queue is doing:

```bash
curl -s localhost:5000/api/queue/status | python3 -m json.tool
docker logs flai-web --tail 100 | grep -i "gpu_lock\|waiting"
```

GPU work is serialized by design. A long-running video or image task blocks chat on purpose, because VRAM is cleaned unconditionally between every GPU task.

---

## Still stuck

Collect this before asking:

```bash
docker compose ps
docker logs flai-web --tail 200
nvidia-smi
curl -s localhost:5000/health | python3 -m json.tool
python3 --version && docker --version
```

Then open an issue at https://github.com/barval/flai with the output. For a suspected regression, note the version from `APP_VERSION` in `app/config.py` and check [../CHANGELOG.md](../CHANGELOG.md) first.