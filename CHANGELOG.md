# Changelog

All notable changes to FLAI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [v11.1] — 2026-09-07

### 🔄 Instant GPU ↔ CPU Switching (No Rebuild)

- **Backend-tagged images** — each compose file now pins its own image tag, so GPU and CPU builds of the same service **coexist** and no rebuild is needed to switch stacks:
  - `docker-compose.gpu.yml` → `flai-sd_cpp:cuda` / `flai-ltxvideo:cuda` (explicit `SD_BACKEND=cuda` / `LTX_BACKEND=cuda` build args)
  - `docker-compose.cpu.yml` → `flai-sd_cpp:cpu` / `flai-ltxvideo:cpu`
- **Why**: before this change both stacks wrote into the shared `flai-sd_cpp:latest` / `flai-ltxvideo:latest` tags — switching CPU↔GPU silently reused the wrong (CPU-built) binary, e.g. image edit running on CPU despite a pass-through GPU.
- **Docs & scripts** — README.md/README-ru.md (CPU-only mode + build sections), Dockerfile headers, and `deploy.sh`/`deploy-ru.sh` usage now document instant switching via `./deploy.sh`/`./deploy.sh --cpu`.

## [v11.0] — 2026-09-06

### 🏗️ Multi-Platform GPU Support (In Progress)

- **Hardware detection abstraction** — new `app/platform_detect.py` detects the compute platform in order: environment override (`FLAI_PLATFORM`), CUDA (`nvidia-smi`), ROCm (AMD), then CPU fallback. Each probe returns VRAM, VRAM used, PCI bus, and driver version. The selected platform is exposed in the `/api/hardware` admin endpoint. AMD/Intel platforms return VRAM=0 and `cuda_detected=False`, which automatically switches the resource manager into CPU-planning mode.
- **NVML dependency removed** — `nvidia-ml-py` replaced by direct `nvidia-smi` parsing in `platform_detect.py` (`_nvidia_probe`).

### ✨ CPU-Only Mode

- **New `docker-compose.cpu.yml`** — identical service stack to the GPU version (web, redis, postgres, llama-swap, SD, LTX, Whisper, Piper, SearXNG, SLM, Qdrant) without NVIDIA runtime, using CPU builds of all models. All timeouts are increased (`SD_CPP_TIMEOUT=1800`, `LLM_TIMEOUT=600`, `SD_CLI_TIMEOUT=3600`).
- **SD (stable-diffusion.cpp) CPU/Vulkan builds** — `Dockerfile.sd_cpp` now has `SD_BACKEND` build arg (`cuda`|`vulkan`|`cpu`), multi-stage build/runtime bases, `ca-certificates` for git clone. `sd_wrapper.py` is backend-aware: uses `--rng std_default` on non-CUDA, skips `--diffusion-fa` and offload flags, applies `SD_CLI_TIMEOUT` (defaults: generate 1800 s, edit 5400 s on CPU).
- **LTX-Video CPU build** — `Dockerfile.ltx_video` now has `LTX_BACKEND` and `LTX_BASE_IMAGE` build args (`python:3.11-slim` + official CPU wheels for CPU, nightly cu128 for CUDA). `ltx_wrapper.py` guards all `torch.cuda.*` calls, uses `LTX_DEVICE=cpu` defaults (256×384, 49 frames, 16 fps — the CUDA defaults are 512×768, 240, 24), reports `device` in metadata.
- **LLM CPU config** — `LlamaSwapConfigGenerator.build_cmd()` now treats `n_gpu_layers=0` as CPU: skips `--flash-attn`, `--kv-offload`, `--cache-type-k/v`, and MTP speculative decoding. Uses `ghcr.io/mostlygeek/llama-swap:cpu` image.
- **Deploy scripts** — `deploy.sh`/`deploy-ru.sh` accept `--cpu` and auto-select `docker-compose.cpu.yml` when NVIDIA is not detected.

### 🎬 Adaptive Video Resolution on CPU (Memory-First)

- **Pre-flight RAM planning for video** — new `plan_cpu_generation()` + `estimate_peak_ram_mb()` in `modules/video.py` estimate the peak LTX memory footprint (`peak_mb ≈ 17400 + units × 3.0`, `units = (w//32)·(h//32)·(frames//8+1)`, +1024 MB safety). The video worker checks free RAM (`MemAvailable`) and, when the requested 768×512×240 clip does not fit, degrades it through a fixed cascade: **384×256×120 @ 12 fps → 256×192×57 @ 6 fps**. The user is notified with the exact chosen format (SSE `notice` + saved assistant message with `system` model name), and generation stops with a clear ⚠️ message when even the smallest step is impossible — nothing ever attempts to run out of memory.
- **Source-image pre-resize for image→video** — `resize_video_source_image()` in `modules/video.py` (max side 768 px, alpha-flattening) is applied in the worker *before* the RAM plan so the resize notice is emitted before the degradation notice; the inline logic previously living in `generate_video()` is now shared.
- **Cascade units** — frame rate is reduced together with resolution (12/6 fps) so the generated clips stay readable, and a `fps`→«к/с» translation key was added.

### 🖥️ Admin Hardware Tab

- **First admin tab «Hardware» / «Оборудование»** — a new tab (before «Users») in the admin panel showing the compute platform (`nvidia`/`amd`/`intel`/`cpu`), GPU, VRAM, CPU cores, RAM, and CPU model. Backed by `/admin/api/hardware`, which now also exposes `cpu_count` and `cpu_name` (CPU model read from `/proc/cpuinfo` in `ResourceManager._detect_cpu_name()`). Rendered client-side by `loadHardware()` in `app/static/js/admin.js`; styled in `admin.css` with light/dark theme support. All labels are localized (`.po` updated and `.mo` recompiled).
- **Tab header replaced by a refresh button** — the static «Hardware» heading was replaced with an «Update hardware list» / «Обновить список оборудования» button (same style as «Update model list»), which re-fetches `/admin/api/hardware`.
- **CPU-only presentation** — when the platform is `cpu` (or no GPU is detected), the GPU and VRAM rows show «—»; a dedicated CPU row (below RAM) displays the CPU model name and core count.

### 🧪 Tests

- **`tests/test_platform_detect.py`** — 14 tests covering NVIDIA, AMD, Intel, and CPU detect paths, including the AMD heuristic that treats values ≤ 1e9 as MB.
- **`tests/test_admin_routes.py::TestAdminHardware`** — 3 tests: full hardware payload (platform, GPU, VRAM, RAM, `cpu_count`), CPU-platform defaults, and that the Hardware tab renders as the first admin tab before «Users».

## [v10.0] — 2026-09-04

### 🏗️ Architecture

- **Two-model architecture: multimodal (always resident) + reasoning (on-demand)** — the standalone "chat" model (Qwen3-4B-Instruct-2507) was removed. The multimodal model (Qwen3VL-8B-Instruct-Q4_K_M) is now the single chat model serving all three roles: LLM router, text chat, and vision (image analysis + image editing). It stays resident in VRAM indefinitely (`ttl=0`, `preload: on_startup`). When a reasoning task runs, the multimodal model is temporarily evicted; after the reasoning response, multimodal is reloaded synchronously before the response is returned to the user. Reasoning model (gpt-oss-20b or user-configurable) uses `ttl=1` and is unloaded 1 second after the last response. Embedding model (bge-m3) unchanged.
- **Model config `module` types reduced to 3: `multimodal`, `reasoning`, `embedding`** — `chat` removed from `MODULE_TYPES` in `app/validators.py`. Database self-healing: `DELETE FROM model_configs WHERE module = 'chat'` runs at startup to clean stale rows from pre-v10.0 backups.
- **Admin panel simplified** — removed the separate "Chat Model" card from the models page (`app/static/js/admin-models.js`). The multimodal model card shows the combined chat/router/vision role.

### ✨ Features

- **Synchronous multimodal reload after reasoning** — after a reasoning task completes, `_preload_multimodal_sync()` in `app/queue.py` sends a completion request to llama-swap and waits (up to 60 s) for the multimodal model to reach running state, ensuring the next router call is instant (no cold start). Replaces the previous background-thread preload.
- **Multimodal model is always resident (`ttl=0`, preload on startup)** — `app/llama_swap_config.py` generates `on_startup` hook that preloads the multimodal model into VRAM at container start, so the first user request gets an instant response.

### 🔧 Improvements

- **Removed VRAM unload/reload from camera and image-chat paths** — `_process_camera_task()`, `_process_camera_task_stream()`, `_process_image_chat_task()`, and `_process_image_chat_task_stream()` in `app/queue.py` no longer unload/reload the multimodal model before/after processing, since multimodal is always resident.
- **Removed measured-VRAM inflation from `get_vram_needed_mb()`** — `app/resource_manager.py` no longer adds 1000 MB safety margin on top of measured VRAM, which caused stale `measured_vram_mb` values to exceed free VRAM and trigger infinite unload/reload loops. Now uses only estimated VRAM (file_size × 1.2).
- **Deploy scripts updated** — `deploy.sh` and `deploy-ru.sh` no longer download the standalone Qwen3-4B chat model. Minimal llama-swap config generates multimodal as `default_model: multimodal` with `ttl: 0`. SD text encoder (Qwen3-4B) still downloaded separately for stable-diffusion.cpp.

### 🐛 Bug Fixes

- **Router proximity block (`-PROXIMITY-`) silently broken** — after the v9.3 release, the proximity detection (contact list matching in `_proximity_router.py`) was not being called because the base module's `_call_router()` method had an incorrect module lookup path. Fixed: corrected the proximity router invocation chain.
- **Tool-service text sanitization** — raw LLM output containing tool-call artifacts was displayed to users. Added `_sanitize_tool_text()` in `app/queue.py` to strip orphaned tool markers from user-facing text.

### 🔧 VRAM Accounting Improvements (mmproj + KV calibration)

- **mmproj (vision encoder) now counted in all VRAM formulas for the multimodal module** — llama-server loads the mmproj file fully into VRAM (`--n-gpu-layers-mmproj` is not configured), so it is a fixed constant independent of `n_gpu_layers`. Previously the multimodal VRAM estimate was ~751 MB short (≈ the Qwen3VL mmproj size), degrading n_gpu_layers on 8 GB GPUs more than needed and under-reserving VRAM elsewhere. `get_mmproj_size_mb()` in `app/utils.py` resolves the mmproj path via `LlamaSwapConfigGenerator` and returns its size. Added to `compute_llamacpp_config()` (budget, "needs more VRAM" and 16 GB branch checks, degradation loop) and `get_vram_needed_mb()`, and threaded through the admin `_estimate_model_vram()` → `_classify_model_fit()` → `model_vram_estimate()` chain (`module` parameter).
- **Per-module KV cache calibration** — `KV_PER_TOKEN_MB` in `app/resource_manager.py` replaces the single generic 0.12 MB/token constant: multimodal 0.10, reasoning 0.08, embedding 0.05 (derived from empirical measurements under the single-resident-model architecture, matching model sizes + overhead). Old constant: multimodal underestimated (−751 MB, missed mmproj) and reasoning overestimated (+636 MB, double-counting the reasoning model's own weight size). Verification on RTX 5060 Ti: multimodal formula ≈ 7938 vs 7912 measured (−0.3%), reasoning ≈ 13065 vs 13084 (+0.15%). `modules/base.py` `context_length` and admin estimates use the same map so the config-time and runtime numbers agree.
- **`.gguf` key lookup bug (n_gpu_layers degradation broken)** — GGUF metadata is cached under model names **without** the `.gguf` suffix (`get_gguf_models_cached`), but `model_configs.model_name` keeps it (e.g. `Qwen3-8B-Instruct-Q4_K_M.gguf`). Pre-fix, `compute_llamacpp_config()` and `_get_original_ngl()` looked up `cache[model_name]` and always missed → `file_size_mb`/`block_count` fell back to hardcoded defaults and `_get_original_ngl()` returned n_gpu_layers=-1 in the degraded (0%) scenario, so the degradation step in llama-swap never applied a concrete layer count. Fixed: strip `.gguf` (`model_name.replace(".gguf", "")`) before cache lookups in `app/resource_manager.py` and `app/llama_swap_config.py`.
- **Multimodality validation for the multimodal module** — the multimodal module requires a vision model (with mmproj). Previously assigning a plain chat model silently broke vision/image-handling. Added server-side validation in `update_model_config()` (`admin.py`) rejecting models without an mmproj file *and* without vision hints from filename/GGUF architecture (`_validate_multimodal_model()`), plus a client-side check in `admin-models.js` `validateModelConfig()` for models classified as `text`. New i18n key `multimodal_model_must_be_vision` added to both `translations/{ru,en}`.

### 🧪 Tests

- **Date-dependent tool tests made deterministic** — `test_time_calc_days_until_end_of_{year,quarter,spring,summer}` in `tests/test_tools.py` asserted `int(result) > 0`, which breaks after the period ends (e.g. "summer" mid-September returned `-5`) and fails on the last day of a period (`0`). They now freeze `pendulum.now` on a fixed date (2026-04-15) via `patch` and assert exact expected day counts (260/76/46/138).
- **Image-chat-stream VRAM test aligned with v10.0 architecture** — `test_gpu_memory_unavailable_uses_system` in `tests/test_image_streaming.py` tested a v9.x path (VRAM wait timeout → reply with `model_name='system'`) that no longer exists: v10.0 keeps the multimodal model always resident and removed `_wait_for_vram()` from `_process_image_chat_task_stream()`. Rewritten as `test_gpu_memory_unavailable_uses_system` verifying the stream path does NOT call `_wait_for_vram` / `_unload_*` and streams a successful reply straight to `_save_and_respond`.

## [v9.3] — 2026-08-19

### 🔒 Security

- **`requests` 2.31.0 → ≥2.33.0** — CVE-2026-25645 fix (HTTP request smuggling via `decode_content`).
- **`pytest` 7.4.3 → ≥9.0.3** — CVE-2025-71176 fix (remote code execution via `--co` option).

### 🔧 Improvements

- **`gunicorn` 21.2.0 → 26.1.0** — eventlet worker removed; gevent worker fully compatible. Config uses `worker_class = "gevent"`.
- **`redis` 5.0.1 → ≥7.0,<9** — redis-py 7.x fully compatible with current API usage (`from_url()`, `pipeline()`, pub/sub). RESP3 remains optional (default RESP2). Redis server 8.4.2 unchanged.
- **`qdrant-client` 1.9.1 → 1.19.0 + Qdrant server v1.12.1 → v1.19.0** — client and server versions synchronized. Standalone deployment compose updated in `services/qdrant/`.
- **`pytest-cov` 4.1.0 → ≥6.0** — latest compatible with pytest 9.x.
- **`pytest-flask` 1.3.0 → ≥1.3.0** — relaxed pin for forward compatibility.
- **mypy clean: 0 errors across `app/` and `modules/`** — added explicit `AbstractLlamaBackend` type annotation for `LlamaCppClient.backend` and removed 5 redundant `# type: ignore[no-any-return]` suppressions in `app/llamacpp_client.py`.
- **`--reasoning-budget` considered** — evaluated `--reasoning_format auto/deepseek` and `--reasoning-budget 2000` for the reasoning model during diagnosis. Not adopted: the prompt fix removes the root cause, and the current `--reasoning_format none` config stays unchanged.
- **Cleaned debug instrumentation from `app/static/js/events.js`** — removed temporary `window.__st`/`window.__rc` counters, title-bar probes, and extra `dlog()` calls added during streaming diagnosis.

### 🐛 Bug Fixes

- **Streaming output freezes during generation (answer appears all at once)** — the server was publishing tokens correctly (Redis pub/sub), but the client main thread was the bottleneck: `onStreamToken()` in `app/static/js/events.js` performed a synchronous sessionStorage write and a full-buffer regex strip + `scrollToBottom` on every token. With ~330 KB of combined output (thinking attempt + answer) this was O(n²), freezing the browser tab and keeping the «⚡ Генерация...» indicator stuck. Fixed: rendering is throttled to 120 ms and sessionStorage writes to 500 ms (capped to the last 16 KB) via `STREAM_RENDER_INTERVAL` / `STREAM_SAVE_INTERVAL` / `STREAM_SAVE_MAX_LEN`.
- **Reasoning retry reloads the model (dead time between attempts)** — the empty-output retry in `_process_reasoning_task()` (`app/queue.py`) called `chat_stream()` which re-ran `_ensure_vram()`, unloading and reloading the reasoning model (~20 s dead time). Fixed: `ensure_vram=False` is passed on the retry attempt (model already loaded), added the flag to `chat_stream()` in `app/llamacpp_client.py` and `generate_reasoning_response_stream()` in `modules/base.py`.
- **Reasoning model returns only thinking (root cause found)** — gpt-oss-20b-mxfp4 is a tool-use-trained reasoning model. When the reasoning prompt mentioned web search (the «В контексте могут быть результаты поиска» rule plus the skills section listing web search), the model entered a tool-call path during its `analysis` channel, and generation was cut off at the `analysis→commentary` channel switch (`finish_reason: stop` right after `<|end|><|channel|>commentary to=`) — leaving only an `analysis` block, which `_strip_thinking_tags()` removes → «No response from reasoning model». Reproduced directly against llama-swap: 25% empty without a trigger, 75% empty with a realistic prompt (history + skills mentioning search), and 0% empty after adding an explicit «do NOT search the web or use tools» rule. Fixed: `prompts/{ru,en}/reasoning.template` now state that web search is performed automatically and its results are already in the context, and that the model must not search or use tools itself. Verified: 12/12 OK without search context and 6/6 OK with search results in context (answers still correctly use the provided results).
- **Camera snapshot returns "GPU memory unavailable" instead of image description** — after a successful camera snapshot, `chat_stream()` re-ran `_ensure_vram("multimodal")` → `ensure_vram_for()`. If llama-swap reloaded a model (preload/TTL) during the 15 s poll loop, `ensure_vram_for()` saw `len(models) > 0` and did `sleep(2); continue` **without re-unloading**, never reaching the VRAM check — timing out with False. Fixed: `chat_with_image_stream()` in `app/llamacpp_client.py` now passes `ensure_vram=False` to `chat_stream()`, eliminating the redundant VRAM check when the caller (queue.py) has already guaranteed VRAM availability via `_wait_for_vram()`.
- **All requests fail with "GPU memory unavailable" (VRAM regression)** — `ensure_vram_for()` timed out when the chat model was already loaded because `get_vram_needed_mb("chat")` returned an inflated value (10467 MB from stale `measured_vram_mb` = 9467 in `model_vram_estimates` + 1000 MB safety margin) exceeding free VRAM (~2600 MB). The function would unload the already-loaded model, llama-swap would immediately reload it via `on_startup` preload, and the poll loop could never reach the threshold. Fixed: (1) `ensure_vram_for()` now returns `True` immediately when the needed model is already loaded — skipping the VRAM threshold check, (2) removed re-unload from the poll loop which caused an infinite unload/reload cycle, (3) reset stale `measured_vram_mb` for the chat model in DB. An external orphaned `llama-server` process (Unsloth Studio, consuming ~13 GB VRAM) was also found and killed as a contributing factor.
- **Router category numbering mismatch (document search unreachable)** — the camera section was dynamically injected as `## 5`, but the document search section in `prompts/{ru,en}/base_text.template` was also `## 5`, creating a duplicate. The MAIN RULE priority references used numbers 5–8 for cameras, documents, internet, and code, while the actual section headers had cameras and documents both at 5. Fixed: camera section now injects as `## 4` (`modules/base.py`), and MAIN RULE references in `prompts/ru/base_text.template` updated from 5–8 to 4–7 to match. The router no longer sees duplicate category numbers, so document search queries (e.g., "Кто такой Валерий Барсуков?") are correctly classified as `[-RAG-]` instead of falling through to simple chat.
- **RAG module never reconnects after startup failure** — `RagModule.init_app()` attempted a single connection to Qdrant; if Qdrant was not yet running (container startup order), `self.available` was permanently set to `False` with no retry. Fixed: (1) `init_app()` now retries 3× with 5s delay at startup, (2) added `check_availability()` method that attempts per-request reconnection when Qdrant was previously unavailable, (3) `search()`, `index_document()`, and `delete_document()` now call `check_availability()` instead of silently returning empty results when Qdrant is down.
- **SLM recall uses two sequential network calls (39s cold start)** — `_get_context_for_model()` in `modules/base.py` called `slm.recall()` twice: once for session-specific facts, then again for general facts. On cold start (after container restart), each call triggered a subprocess init taking ~25s+14s = 39s total, even when returning 0 facts. Fixed: merged into a single `slm.recall(limit*2)` call with local filtering by `fact_type`, cutting cold start penalty in half (~25s vs ~39s). Additionally, semantic recall timeout reduced from 30s to 15s to fail faster on cold starts.
- **RAG search broken after qdrant-client upgrade (1.9.1 → 1.19.0)** — `QdrantClient.search()` method was removed in qdrant-client 1.19.0, replaced with `query_points()`. This caused all RAG searches to fail with `'QdrantClient' object has no attribute 'search'`, falling back to reasoning without document context. Fixed: replaced `search()` with `query_points()` in `modules/rag.py` (search method) and `app/routes/debug.py` (debug endpoint). Parameter `query_vector` renamed to `query`; result accessed via `.points` attribute.
- **Router misclassifies person-name queries as web search instead of document search** — queries like "Кто такой Валерий Барсуков?" with first+last name were inconsistently routed to `[-SEARCH-]` (web) instead of `[-RAG-]` (documents) by the LLM router. The router had no knowledge that user documents might contain information about specific people, so it defaulted to the more general web search category. Fixed: added explicit name rule to `prompts/{ru,en}/base_text.template` MAIN RULE section ("if query contains PERSON'S FIRST + LAST NAME → ALWAYS category 5, NOT category 6"), strengthened category 5 examples with name-based queries, and added exclusion note to category 6.
- **Tool calls with single-quoted JSON not parsed** — small LLM models (e.g. Qwen3-4B) sometimes output tool calls as Python-style dicts with single quotes (`{'name': 'get_current_time', 'arguments': {}}`) instead of valid JSON with double quotes. `json.loads()` in `_try_parse_text_tool_call()` rejected these, causing raw dict text to be shown to the user instead of executing the tool and generating a response. Fixed: added `ast.literal_eval()` fallback when `json.loads()` fails in `app/queue.py`.
- **Image generation shows double progress bar (OOM retry)** — sd-wrapper always starts with `offload_level=0` (all models on GPU). On RTX 5060 Ti (16 GB), this always OOMs: model weights (~10 GB) + VAE decode buffer (~6.5 GB for 1024×1024) = 16.6 GB > 16.3 GB VRAM. The retry with `offload_level=1` (clip on CPU, ~13 GB) succeeds, but each attempt sends its own sd_step callbacks → client renders two progress bars. Root cause: VAE decode buffer scales with output resolution and was not accounted for in the VRAM budget. Fixed: (1) added `_estimate_total_sd_vram()` in `modules/sd_cpp.py` that computes model weights + VAE decode buffer (empirically measured at 6657 MB for 1024×1024, scales linearly with pixel area), (2) added `_choose_start_offload_level()` that selects the lowest offload level fitting in available VRAM, (3) sd-wrapper now accepts `start_offload_level` parameter, skipping levels guaranteed to OOM. On RTX 5060 Ti: starts at level 1 (~70s, one clean progress bar). On 24 GB+ GPUs: starts at level 0 (maximum speed). (4) Client-side safety net in `onImageStep()` ignores backward step resets as a fallback.

## [v9.2] — 2026-07-31

### 🐛 Bug Fixes

- **Reasoning model returns empty output (thinking-only) → retry once** — gpt-oss-20b occasionally generates only an `analysis` reasoning block (`<|channel|>analysis<|message|>…<|end|>`) without a final `commentary` answer. This happened especially on cold model loads (first generation right after just-in-time model load), producing ~1s of pure reasoning with no answer. After `_strip_thinking_tags()` the response was empty → immediate «No response from reasoning model» error (message 3193, 2026-07-31 11:58:11). Fixed: `_process_reasoning_task()` in `app/queue.py` now retries the generation once when the stripped output is empty. The retry is fast because the first attempt already loaded the model. No retry on task cancellation or on genuine LLM error strings; a second empty result still returns the error (now with a `Reasoning model returned empty output (attempt 1), retrying once` warning in logs).

## [v9.1] — 2026-07-06

### ✨ Features

- **Web search page content extraction** — When SearXNG returns search results with empty `content` (no snippet), the system now downloads each page via HTTP and extracts readable text using `trafilatura`. Previously, the reasoning model received only URLs and titles, causing it to answer "I don't have access to current information". Now real article text is extracted and passed as context. Added `_fetch_page_content()` method in `modules/search.py`, `trafilatura>=2.0.0` dependency.
- **Blackwell-aware model deployment** — Chat and reasoning models are now auto-selected based on GPU architecture. MXFP4 variants (`Qwen3-4B-Instruct-2507-MXFP4_MOE`, `gpt-oss-20b-mxfp4`) are downloaded/configured on Blackwell GPUs (RTX 5060+, native FP4 tensor cores). Standard Q4_0/Q4_K_M quantizations are used on other NVIDIA GPUs (Ampere, Ada Lovelace) for optimal prefill performance. Detection via `nvidia-smi --query-gpu=name`. Covers: deploy scripts (`deploy.sh`, `deploy-ru.sh`), seed DB (`app/database.py`), fallback models (`app/tasks/dry_load.py`), and `is_blackwell_gpu()` utility (`app/utils.py`).

### 🐛 Bug Fixes

- **RAG search context discarded when budget exceeded** — `_get_context_for_model()` in `modules/base.py` returned only `slm_facts_str` (or empty string) when `remaining_for_history <= 0`, silently dropping the entire search context. With `categories=general,news`, 7 results could total 36 456 chars / 10 330 tokens, exceeding the 10 444 token budget. The reasoning model received NO search results and generated only thinking tags → `_strip_thinking_tags()` → empty string → «No response from reasoning model». Fixed: `format_results_context()` now truncates per-result to 2 000 chars and caps total dynamically via `get_search_context_limit()` (~30% of effective budget, ~11 K chars for 16 K context); `_get_context_for_model()` now returns RAG+SLM (without history) instead of empty when budget is exceeded.

- **`<|channel|>` thinking tokens: `analysis` stripped, `commentary` unwrapped** — gpt-oss-20b uses two channel types: `analysis<|message|>` for reasoning (strip entirely) and `commentary<|message|>` for the actual answer (unwrap — keep content, remove tags). Previous fix stripped all `<|channel|>` blocks, killing search results that gpt-oss-20b delivers via `commentary`. Now `_THINK_OPEN_RE` is specific to `analysis<|message|>` only; `_strip_thinking_tags()` unwraps `commentary` blocks and strips malformed `<|channel|>...` without `<|message|>`. `<|channel|>` is intentionally **not** a stop token — gpt-oss-20b always starts generation with it.
- **`_strip_thinking_tags()` dangerous regex in `app/queue.py`** — Second regex `r"<\|channel\|>analysis<\|message\|>[\s\S]*$"` deleted the entire response when gpt-oss-20b produced an `analysis` block without a closing `<|end|>` tag (happens with large search contexts, ~600+ tokens). Replaced with `r"<\|channel\|>analysis<\|message\|>(?:[\s\S]*?<\|end\|>)?"` — non-greedy, optional `<|end|>`, never deletes beyond the tag boundary.
- **Buﬀer ﬂush in `app/llamacpp_client.py`** — `_process_stream_chunk` deferred output when `_thinking_active=True`, discarding tokens that arrived after the `analysis` block ended but before the `commentary` block began. Now performs unconditional buffer flush when `_thinking_active` transitions from `True` to `False`.
- **Router URL→SEARCH misclassification** — Router (Qwen3-4B) classified queries containing URLs (e.g. `example.com`, `github.com`) as `[-RAG-]` instead of `[-SEARCH-]`. Added explicit URL examples in SEARCH category and exclusion in RAG category in `prompts/{ru,en}/base_text.template`.
- **Ruff false positives on JS files** — `ruff check .` reported 3177 errors in `events.js` (ruff cannot parse modern JS). Excluded `"*.js"` from ruff. Replaced non-ASCII dashes (`─`, `—`) with ASCII `-` in `events.js`.
- **`_strip_generic_reasoning()` returning empty string** — When a small model (Qwen3-4B) produced reasoning markers followed by a short answer (<100 chars), the function returned `""` instead of the answer. Now returns original `text` if parsed answer is empty, never discarding valid responses.
- **Tool invocation instruction without tools** — System prompt contained "use tools when necessary — do not make up an answer, call a tool instead" but tools were `null`, causing models to cite skills instead of answering directly. Both the tool invocation instruction and `time_calc` examples are now conditional on `include_tools=True`.
- **`expose_tools` parameter** — Separates tool definitions from system prompt content. Category 1 (fast chat) now uses `include_tools=False` (short prompt without time/tool sections) + `expose_tools=True` (model can still call tools). Fixes date calculations like "when does the next 16th of the month fall on a Thursday?" returning wrong year.
- **Emoji missing from stage messages** — Transition from hardcoded emoji in `STAGE_LABELS` (v9.0) to i18n via `t()` lost emoji in all 9 stage messages (⏳ 🔍 🎬 🎨 ✏️ 🧠 📹). Restored in `.po` files for both RU and EN locales. Added bind mount `./translations:/app/translations` in `docker-compose.gpu.yml` so `.mo` changes persist through container restarts.
- **Router system message missing 3 categories** — System message in `modules/base.py:369` only listed SIMPLE/REASONING/IMAGE/VIDEO/CAMERA but omitted SEARCH, RAG, and REMEMBER. Router (Qwen3-4B) frequently misclassified search queries (e.g., "Find the description and price of the Poco X8 Pro smartphone in DNS") as category 1, causing chat model to hallucinate answers instead of performing web search. Updated to explicitly list all 8 categories from `base_text.template`.
- **`_strip_generic_reasoning()` false positives on Russian text** — `Need`, `Should`, `Must`, `Analysis`, `Check`, `Formulation`, `Identification`, `Consideration`, `Plan`, `Comment`, `Correction`, `Clarification` were regex patterns matching ordinary Russian words in informative responses, causing the function to strip actual answer content. Removed overbroad Russian patterns, kept only clearly reasoning-specific markers (`I need to`, `User asked`, `Final answer generation`, etc.). Raised threshold from `>=1` to `>=2` markers to match JS client. Removed redundant server-side `_strip_generic_reasoning()` call from `queue.py` — filtering now happens only at display time.
- **Reasoning model hallucinates web search results** — When web search was used ([-SEARCH-] route), the search context (2115 chars of real results) was correctly injected into `conversation_history` via `_get_context_for_model()`, but: (1) the heading always said "Found information from documents" regardless of source — the reasoning model treated search results as irrelevant document context; (2) `reasoning.template` had a dead `{rag_context}` placeholder that was always empty string; (3) no anti-hallucination instruction told the model to base its answer on provided context. Result: model generated 13K tokens of completely fabricated news ("Siri AI Core", "PaLM-3", "Medical-Data-X") while ignoring real search data. Fixed: web search context now gets a prominent heading ("Web search results — USE ONLY THIS DATA"), anti-hallucination rules added to `reasoning.template` (ru + en), dead `{rag_context}` removed from template and `format_prompt()` calls.
- **TTS reads markdown formatting aloud** — `**bold**` and `*italic*` in chat responses were sent verbatim to Piper TTS, causing asterisks to be spoken ("star-star"). Added `clean_markdown_for_tts()` in `app/utils.py` that strips bold, italic, code, links, images, headings, quotes, lists, HTML tags, and orphaned `**` fragments (from sentence-split at `.` inside URLs like `**NN.RU**`). Called in `modules/tts.py:synthesize()` before sending to Piper. Added `modules/tts.py` bind mount to `docker-compose.gpu.yml`. 22 tests in `TestCleanMarkdownForTTS`.
- **SLM stores model responses as user facts** — `slm_rules.py` extracted facts from the model's response (`response`) instead of the user's query (`query`). Pattern `I work` matched the model's "How can I help?" (contains "I" + verb), and `score >= 0.40` threshold was too low for typical assistant outputs. `slm_import.py` imported `role='assistant'` messages into SLM, causing 289 junk facts for user valery (~5 real). Fixed: extract from `query` not `response`, added `_MODEL_RESPONSE_PATTERNS` filter for typical assistant outputs, raised threshold to `0.50`, removed bonus for "I+verb". Import now skips `role='assistant'` (except direct user quotes). Merge pipeline got step 0: `_is_model_response()` cleanup. Added `flask cleanup-slm --user <id>` CLI command.
- **SLM facts not deleted when session deleted** — Three bugs: (1) `fact["id"]` in sessions.py used wrong key (SLM facts use `fact_id`), causing `KeyError` silently caught; (2) SLM daemon does not store `session_id` in `atomic_facts` (all empty), so filtering by metadata was a no-op; (3) `clear_history` did not touch SLM at all. Fixed: new `_delete_session_facts_from_slm()` matches facts against session's user message contents (exact match or substring), works regardless of metadata. Both `delete_session` and `clear_history` now clean up SLM facts. Added bind mounts for `cli.py`, `slm_rules.py`, `slm_import.py`, `slm_merge.py`, `routes/sessions.py`.

### 🔧 Improvements

- **Docker volumes simplified** — Replaced 35 individual file bind mounts (`app/*.py`, `modules/*.py`, `prompts/*.template`, `app/static/js/*.js`, `app/static/css/*.css`) with 3 directory mounts (`./app:/app/app`, `./modules:/app/modules`, `./prompts:/app/prompts`). All source code is now hot-reloaded without rebuild. Added `PYTHONDONTWRITEBYTECODE=1` to prevent `__pycache__` pollution on the host. Volumes section reduced from 36 to 11 lines in `docker-compose.gpu.yml`.
- **Enhanced web search results quality** — Three fixes to make news queries actually return useful content: (1) `_fetch_page_content()` runs via `ThreadPoolExecutor` (max 3 concurrent) for all results with short snippets (<300 chars), not just empty ones; (2) Instructions softened from «USE ONLY THIS DATA» to «use this data as your primary source» in both `modules/base.py` heading and `prompts/{ru,en}/reasoning.template`, allowing the model to supplement with its own knowledge when search data is insufficient; (3) SearXNG initially used `categories=general,news` for article-level results, but this was later removed as it caused DuckDuckGo rate limiting on SearXNG (CAPTCHA errors).

## [v9.0] — 2026-06-16

### ✨ New Features

- **Video frame policy** — Default 240 frames (10 sec @ 24 fps), full 768×512 landscape. Capped to 120 frames at 512×512 when VRAM < 10GB or available VRAM < 6GB.
- **Streaming reasoning** — `generate_reasoning_response_stream()` yields tokens one-by-one. Server-side `_strip_thinking_tags()` and client-side `_stripThinkingTags()` remove thinking blocks.
- **Camera rooms CRUD** — `app/cameradb.py` + `app/morph.py` (pymorphy3 Russian morphological analysis). Admin UI in `admin-cameras.js`. DB table `camera_rooms` with declension forms.
- **Generation progress bars** — SSE events `task_progress`, `video_step`, `image_step`, `image_preview`. Progress persisted in Redis with 30 min TTL. Client restores on reconnect.
- **Task cancellation** — Cancel button in streaming messages. Redis flag `task:cancel:{task_id}`. SSE event `stream_cancelled`.
- **Combined voice + image recording** — Voice stored as `attachedVoiceBlob` when image already attached. Server creates `type: "image"` task.
- **DOMPurify XSS protection** — All `marked.parse()` output goes through `DOMPurify.sanitize()` before DOM insertion.
- **Lazy loading** — All `<img>` and `<video>` elements with `loading = 'lazy'`.
- **Run HTML button** — `handleOpenHtmlClick()` opens HTML code blocks in new tab.
- **Copy message text** — `copyToClipboard(text)` with `execCommand('copy')` fallback.
- **MTP factor in VRAM estimation** — `mtp_factor = 1.15 if supports_mtp else 1.0`.
- **GGUF fallback reading** — Reads `block_count` and `expert_count` directly from GGUF file if not in cache.

### 🐛 Bug Fixes

- **Chat temperature too low for style sensitivity** — DB default for chat model was `temperature=0.1, top_p=0.1` (inherited from router classification values), making style instructions ineffective. Updated DB defaults to `temperature=0.7, top_p=0.9` (matching reasoning model). Router now uses explicit `temperature=0.1` hardcoded in `process_message()` calls. SLM `remember` task also uses explicit `temperature=0.1` for deterministic fact extraction.
- **STYLE_INSTRUCTIONS duplicated in 3 modules** — Identical style maps existed in `modules/base.py`, `modules/rag.py`, and `modules/multimodal.py`. Removed duplicates from `rag.py` and `multimodal.py`; both now import `STYLE_INSTRUCTIONS` from `base.py`.
- **Context window overflow risk with SLM** — `_get_context_for_model()` reserved a hardcoded 490 tokens (7 × 70) for SLM facts, but actual fact sizes could exceed this, stealing space from conversation history. Now fetches SLM facts first, measures real token cost, then calculates history budget with the actual SLM size subtracted.
- **Translation system** — Removed `.mo` volume mounts that were overriding correct compiled translations. Docker now properly compiles all translations at build time.
- **Queue position** — Removed `pendingRequestIds` race guard from `chat-queue.js`. Queue positions now come exclusively from server data.
- **Multi-tab session** — Client now sends `session_id` in request body. Server validates (UUID v4 + user ownership). Fixes race conditions between browser tabs.
- **Error message prefix** — All error messages now start with `"⚠️ "`. Added `_is_llm_error_string()` helper.
- **Error header missing "⚠️ system"** — `_build_error_response()` did not include `model_used`/`model_type` in the returned dict, so `finalizeStreamedMessage()` on the client could not render the "⚠️ system" label in the message header. Added both fields to the error response dict.
- **Multimodal context overflow** — Vision models tokenize images into far more tokens than the 1000-token estimate in `_validate_prompt()`. Updated estimate to 4096. Also increased multimodal model `context_length` from 8192 to 16384 (safe since multimodal and video pipelines never share VRAM). Migration updates existing deployments.
- **SLM fact merge never executed** — `_start_slm_merge_watcher()` imported `get_all_user_ids` from `app.userdb` but the function did not exist, causing `ImportError` on every watcher tick. Added the function and deduplication guard (`_merge_last_queued`) to prevent duplicate merge tasks during prolonged idle.
- **SLM fact quality** — Updated `slm_extract.template` (RU/EN): max 200 chars per fact, skip user commands and full LLM responses. Updated `slm_merge.template`: merge semantically similar facts, delete command fragments. Added pre-filter in `slm_extract.py` to skip extraction for commands/greetings. Cleaned 80 garbage facts for user valery (188→108 active).
- **SLM memories cleanup** — Admin panel now counts active SLM facts via `atomic_facts WHERE lifecycle='active'` instead of raw `memories` table count. Added `_cleanup_memories_for_user()` to remove orphaned `memories` rows (where no `atomic_facts` has `lifecycle='active'`). Added `/cleanup-memories` POST endpoint for manual cleanup. Added `_periodic_cleanup()` daemon thread that runs every hour to clean orphaned memories automatically.
- **Web search config** — Removed hardcoded `max_results=5` and `MAX_WEB_SEARCH_RESULTS=5000` from tool executor. Both are now configurable via `SEARXNG_MAX_RESULTS` (default 7) and `SEARXNG_MAX_RESULTS_CHARS` (default 7000). RAG char limit configurable via `RAG_MAX_RESULTS_CHARS` (default 5000).
- **RAG architecture** — RAG on fast worker now does ONLY search (`rag.search()`). Answer generation via reasoning model happens EXCLUSIVELY on slow worker. Prevents GPU contention with LTX-Video.
- **.env sync rule** — Added explicit documentation that `.env` and `.env.example` must be kept in sync (same sections, same variables; `.env` has real values, `.env.example` has placeholders). Updated AGENTS.md, RELEASE_GUIDE.md, ARCHITECTURE.md.
- **userdb.py schema mismatch** — `delete_user()` used `user_id = user["id"]` (INTEGER) against TEXT columns. Fixed to `user_id = login` (TEXT).
- **test_backups.py** — Added `Babel(flask_app)` to fixture. Fixed `shutil.copytree FileExistsError` via `dirs_exist_ok=True`.
- **mypy app/utils.py** — 19 → 0 errors via `_gguf_scalar()` helper.
- **`call_llamacpp()` missing `temperature` parameter** — `BaseModule.call_llamacpp()` and `LlamaClient.call()` did not propagate `temperature` to `chat()`. Fact extraction, SLM extract, and SLM merge functions passed `temperature=0.1` which raised `TypeError`. Added parameter to both methods with proper passthrough.
- **`KeyError('text')` in fact extraction** — `_process_fact_extraction()` accessed `f["text"]` on SLM facts that may lack the key. Changed to `f.get("text", "")` with filtering. Same fix applied in `slm_extract.py`.
- **Background task errors leaking to users** — `_process_fact_extraction()` and `_process_fact_merge()` were not wrapped in try/except. Any exception (network error, parse error, LLM failure) was caught by `_process_single_task` and published as an SSE error event, causing `⚠️ Error: ...` messages to appear in the user's chat after a successful response.
- **Phantom ⚡ after chat response** — `fact_extraction_task` (enqueued on slow worker after every chat response >20 chars) had the same `session_id` as the main task. `get_user_requests_status()` reported it as `processing`, causing the lightning bolt to reappear. Background tasks are now excluded from queue status display.
- **Negative queue counter** — `fact_extraction_task` was added directly to slow queue via `redis.rpush()` without `add_request()`, but `_process_single_task()` always called `_decrement_user_queue_count()` in its `finally` block. After N responses, `user_counts[user_id]` drifted to -N (e.g. `-9`), causing displays like `📊 -9/0`. Fixed by skipping decrement for background tasks and adding `max(0, ...)` guard in `get_user_queue_counts()`.
- **flash-attn SIGABRT on Blackwell GPUs** — `--flash-attn on` with `--n-gpu-layers > 0` (partial offloading) caused SIGABRT on Blackwell sm_120 GPUs (llama.cpp build 9294). Flash-attn is now disabled when partial offloading (ngl > 0). Effective ngl is computed before the flash-attn logic to ensure correct decision.
- **500 errors causing unnecessary model degradation** — `LlamaSwapBackend.call()` only retried on 502 but not 500, causing transient llama-swap errors to trigger `degrade_and_reload()` on the first failure. Now retries on both 500 and 502 (`response.status_code in (500, 502)`).
- **Gunicorn workers 2→1** — `threading.Lock()` (`_gpu_lock`) only works within a single process. With 2 gunicorn gevent workers, GPU tasks could run concurrently across processes. Reduced to 1 worker. Single worker is optimal for GPU-bound workloads.
- **Preload after fresh YAML** — `generate_and_write()` now accepts `include_preload` parameter. On initial startup: `include_preload=False` (prevents crash from stale on-disk YAML). On admin reload and dry_load: `include_preload=True` (preloads model before first request).
- **Merge watcher queue flooding** — SLM merge watcher now checks `llen(slow_queue_key) > len(user_ids)` before enqueueing, preventing redundant merge tasks from flooding the queue during prolonged idle.
- **Generic reasoning output visible to user** — Some models (gemma-4-E2B, gpt-oss-20b) output chain-of-thought as plain text without `<thinking>` tags. `_strip_generic_reasoning()` detects common reasoning markers (e.g. "Analyze Persona:", "Final Answer Generation:") and strips everything up to the actual answer. Applied to ALL model types server-side (queue.py, llamacpp_client.py) and client-side (events.js). Chat and reasoning templates (RU/EN) updated with explicit instruction: "Write ONLY the final answer. Do NOT write reasoning, analysis, thinking steps."
- **Double ⚠️ error prefix** — Server `_build_error_response()` and client `events.js` both prepended "⚠️ " to error messages, resulting in "⚠️ ⚠️ ..." display. Removed redundant prefix from client-side `onError()` and `onStreamCancelled()` handlers.
- **llama-swap errors not translated** — Raw llama-swap error messages (e.g. "model not found", "context size exceeded") were displayed to users in English. Added `_translate_llama_swap_error()` in `llamacpp_client.py` with RU/EN translations for common errors.
- **Database migration .gguf suffix** — `database.py:327` migration for chat model name update didn't include `.gguf` suffix variant, causing old Gemma model names to persist in some deployments.
- **Chat auto-scroll broken** — `_isLoadingMessages` flag added to `chat-messages.js` to prevent N competing async scroll callbacks. `isNearBottom()` threshold increased to 200px. `scrollToBottom()` simplified. `overflow-anchor: none` added to chat container CSS.
- **Cancel button missing for image/video** — Cancel button (■) only appeared for streaming text tasks. Added cancel support for image generation, image editing, and video generation tasks. Backend: `_is_task_cancelled()` polls Redis `EXISTS task:cancel:{id}`. Frontend: `_showHeaderCancelButton()` in `onTaskProgress()`, `onVideoStep()`, `onImageStep()`. Video cancel uses background checker thread + container restart to interrupt mid-generation.

### 🔧 Improvements

- **Stronger style instructions** — All 5 response styles (`neutral`, `academic`, `professional`, `friendly`, `funny`) now include explicit prohibitions (`Do NOT use...`) to improve style adherence by small local models.
- **Context logging enhanced** — `_get_context_for_model()` now logs SLM fact count and token cost separately: `Context loaded: 12 history msgs, 5 SLM facts (387 tokens), 8421 tokens (25.6% of 32768)`.
- **Dead code cleanup** — Removed `get_gguf_model_info()`, `find_gguf_file()`, `chunk_text_by_sentences()`, `clear_camera_rooms()`, `get_database_type()`, `is_postgresql()`, `close_db()`, `generate_chat_response_stream()`. Removed CSS classes `.capabilities`, `.capability`.
- **Chat video export** — `saveChatAsHTML()` collects `<video>` elements, fetches video files, converts to base64. Video rendered as `<video controls preload="metadata">`.
- **Dead torch code cleanup** — Removed all `torch.cuda.empty_cache()` and `torch.cuda.synchronize()` calls (~60 lines). `flai-web` has no CUDA context.
- **SLM orphaned memories cleanup** — Daemon's `memories` table grows indefinitely but is never read by the system (only `atomic_facts` is used). Added `_cleanup_memories_for_user()` that removes `memories` rows with no active `atomic_facts` (safe via FK CASCADE). Added `/cleanup-memories` POST endpoint and `_periodic_cleanup()` daemon thread (hourly). For valery: 270→70 memories (200 orphaned removed).
- **SLM rule-based fact extraction** — Replaced LLM-based extraction with pattern matching (`app/slm_rules.py`). Scoring by category patterns (preferences, facts, instructions, personality). No GPU usage, ~50-200ms CPU. Semantic deduplication via new `/similarity` endpoint.
- **SLM rule-based fact merge** — Replaced LLM merge with edit-distance + semantic similarity + temporal decay pipeline. No CPU LLM usage, ~100-500ms. Auto-archives facts older than 90 days with low confidence.
- **Fact extraction moved to background thread** — `_extract_facts_bg()` runs as `threading.Thread(daemon=True)` instead of enqueueing to slow worker. Eliminates GPU lock contention for fact extraction.
- **Skills list centralized** — All skills/capabilities text extracted to `prompts/{ru,en}/skills.txt` as single source of truth. `format_prompt()` auto-injects `{skills_section}` when the template contains the placeholder. Previously skills were duplicated (and inconsistent) across `chat.template`, `reasoning.template`, `rag.template`, `image_text.template`, and inline Python code in `queue.py`. Now 10 files (8 templates + 2 master copies) always show the same 10 skills.
- **`_process_chat_with_tools()` skills from master file** — Inline system prompt in `_process_chat_with_tools()` now loads skills via `_load_skills_section()` instead of a hardcoded list that could drift from the templates.

### 📦 Dependencies

- Added `pymorphy3>=2.0.6` for Russian morphological analysis.

### 📝 Migration Notes

- `camera_rooms` table added. Migration `migrate_name_forms()` regenerates existing room forms with pymorphy3 on startup.
- `model_vram_estimates` table PK changed from `(module)` to `(module, model_name)`. Idempotent migration in `init_db()`.
- Admin panel SLM facts count now reflects `atomic_facts WHERE lifecycle='active'` instead of total `memories` rows — may show different numbers for existing deployments.
- New env vars: `SLM_SIMILARITY_THRESHOLD` (0.85), `SLM_TEMPORAL_DECAY_DAYS` (90), `SLM_MIN_CONFIDENCE_FOR_DECAY` (0.5). `MERGE_CONTEXT_SIZE` and `MERGE_MAX_FIT_FACTS` no longer used (kept for backward compat).

---

## [v8.8] — 2026-05-20

### ✨ New Features

- **3-tier model protection** — Admin panel classifies models as `good`, `cpu_offload`, `impossible`, or `unknown` based on VRAM/RAM fit. Prevents OOM when saving model configs.
- **Dynamic VRAM estimation** — Computes VRAM from GGUF metadata (file_size, block_count), DB config (context_length), and n_gpu_layers. No hardcoded constants.
- **Real VRAM measurement** — `measure_model_vram()` captures actual VRAM consumption after each successful model load. Stored in `model_vram_estimates` table.
- **Per-model-type circuit breakers** — Separate CB for chat, reasoning, multimodal, embedding. One model's failures don't block another.

### 🐛 Bug Fixes

- **CUDA OOM on video generation** — `_wait_for_vram()` redesigned: changed from `≥80% GPU threshold` to `video_needed + 3GB buffer`. Timeout increased 30→60s.
- **HTTP 502 on reasoning queries** — `ensure_vram_for_reasoning()` now returns error on timeout instead of proceeding into OOM.
- **Video OOM persisted** — Multimodal unloads (TTL=1s) before LTX-Video loads.
- **Hardcoded VRAM constants** — Replaced with dynamic estimation via GGUF metadata.
- **Buffer increase +500→+3000MB** — In `_resolve_use_gpu()` for safety margin against CUDA fragmentation.
- **Phantom measurement fix** — `model_vram_estimates` PK changed to `(module, model_name)`. Each model gets its own row.
- **LTX-Video unload optimization** — Pre-flight check, 30s result cache, reachable success condition, Docker restart on 3 consecutive timeouts.
- **Retry for reasoning on 502** — LlamaSwapBackend retries reasoning requests once on 502, with automatic model degradation on first failure.

### 🔧 Improvements

- **RAG improvements**:
  - Router template updated: added category 5 for document/person/age/biography queries → `[-RAG-]`
  - RAG call added to streaming reasoning path
  - Strict threshold lowered 0.7→0.5 (higher recall)
  - RAG context in reasoning prompt
  - RAG retry in `_process_reasoning_request`
  - RAG prompt fixed: "use ONLY the provided context. If context doesn't contain the answer — honestly say you cannot find it."
  - Raw chunks passed to reasoning model on RAG failure
- **Dead torch code cleanup** — Removed ~60 lines of no-op `torch.cuda.*` calls.

### 📝 Migration Notes

- `model_vram_estimates` table PK changed. Idempotent migration in `init_db()` via `DO $migrate$` block.
- RAG on fast worker now does ONLY search. Answer generation moved to slow worker.

---

## [v8.0] — 2026-04-01

### ✨ Initial Release

- Multimodal AI assistant on single consumer GPU
- Flask + PostgreSQL + Redis
- llama.cpp with llama-swap proxy
- RAG with Qdrant
- SLM (SuperLocalMemory) for long-term memory
- Tool calling (6 tools)
- Web search (SearXNG)
- TTS (Piper) + STT (Whisper)
- SD image generation + LTX-Video
- Admin panel for model management
- i18n (English + Russian)