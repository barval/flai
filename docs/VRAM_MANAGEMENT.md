# VRAM Management — FLAI v9.0

This document describes the VRAM management system, GPU queue rules, and model protection mechanisms. Read it when modifying `resource_manager.py`, `queue.py`, video/multimodal pipelines, or admin model configuration.

For critical rules summary, see the root `AGENTS.md`.

## Centralized VRAM Management

`app/resource_manager.py` provides two core methods:

### `get_vram_needed_mb(model_type)`

Computes VRAM needed from:
- GGUF metadata (`file_size`, `block_count`)
- DB config (`context_length`)
- `n_gpu_layers`

**Formula**: `file_size × (ngl/block_count) × moe + ctx_size × kv_factor + overhead`

**No hardcoded constants.** Uses actual model file size, layer count, and context window from DB.

### `ensure_vram_for(model_type)`

- Unloads ALL models
- Flushes CUDA cache
- Polls `/running` + `nvidia-smi` (60s timeout)
- Returns `False` (never proceeds) if VRAM insufficient
- Used by ALL model types: chat, reasoning, multimodal, embedding

## Dynamic VRAM Estimation

`_estimate_model_vram()` in `app/routes/admin.py`:
- Accepts `supports_mtp: bool`
- Formula: `mtp_factor = 1.15 if supports_mtp else 1.0`
- MTP draft prediction layers add ~15% overhead to model weights in VRAM

### Real VRAM Measurement

`measure_model_vram()` captures actual VRAM consumption after each successful model load and stores it in `model_vram_estimates` table with measurement count and context-length metadata.

**Admin panel displays**:
- `"✓ VRAM: X MB / Y MB — measured (N measurements)"`
- `"ℹ VRAM: ~X MB — estimated"` with color-coded percentage bars

### Phantom Measurement Fix (v8.8+)

`model_vram_estimates` table PK changed from `(module)` to `(module, model_name)`. Each model now gets its own row — switching to a new model in the same module no longer inherits phantom measurements from the old model.

- Idempotent migration in `init_db()` via `DO $migrate$` block.
- `get_vram_estimate(module, model_name=None)` accepts optional `model_name` for exact match.
- `upsert_vram_estimate()` queries by `(module, model_name)`.
- Admin endpoint (`/admin/api/model-estimate`) passes `model_name` to `get_vram_estimate()`.

## 3-Tier Model Classification

`app/routes/admin.py:_classify_model_fit` — three-tier model-fit classification used in the admin panel to prevent OOM when saving a model config.

| Tier | Condition | UI | Server | Result |
|------|-----------|-----|--------|--------|
| 🟢 `good` | `vram_needed ≤ 85% × total_vram` | Green "Fits in VRAM" | Save OK | Full ngl on GPU |
| 🟡 `cpu_offload` | `vram_needed > 85%` AND `(file - gpu_weights) ≤ 70% × ram - 2GB` | Yellow "Partial CPU offload, ~5-10× slower" | Save OK with auto-degraded ngl | ngl recomputed to fit VRAM |
| 🔴 `impossible` | `(file - gpu_weights) > 70% × ram - 2GB` | Red "Cannot be loaded" | **400 block** | ngl=0, model can't fit anywhere |
| ⚠ `unknown` | Model not in `gguf_models_cache` | Orange "Run Refresh models first" | **400 block** | No metadata to compute fit |

### Threshold Formula (v8.8+)

```python
TIER_VRAM_GOOD_PCT = 0.85     # 85% of total VRAM is "good" budget
TIER_RAM_SAFETY_PCT = 0.70    # 70% of system RAM allowed for model + KV
TIER_RAM_HEADROOM_MB = 2048   # 2GB reserved for OS + other processes
```

`arch_max_ctx` from `gguf_models_cache.context_length` is the architectural cap (Qwen3 = 262144, gpt-oss = 131072). Upper limit is dynamic, no hardcoded 32768.

## 5-Layer Protection
  1. **UI hint** (`app/static/js/admin-models.js:updateMemoryEstimation`): on model/ctx change, fetches `/admin/api/model-estimate` and displays colored tier indicator. Save button is **disabled** when `can_save=false`.
  2. **Server validation** (`app/routes/admin.py:update_model_config`): before saving, calls `_classify_model_fit()`. If `tier=impossible` or `tier=unknown` → returns 400 with `tier_message`. Defense in depth (UI is bypassable).
  3. **Dry-load + auto-rollback** (`app/tasks/dry_load.py`): after successful `signal_reload()`, schedules a background thread that:
    + Sends tiny completion to llama-swap to trigger model load
    + Polls `/running` for 30s waiting for model
    + On success: unloads test instance (next user request reloads)
    + On failure: calls `_rollback()` to restore `FALLBACK_MODELS[module]`
  4. **Crash loop watchdog** (app/tasks/health_monitor.py): 60s polling loop:
    + Reads llama-swap `/running` for active models
    + Sends tiny health check to each
    + Records failures in 5-min sliding window
    + On 3 failures in window → auto-rollback to fallback
  5. **File size + context validation:**
    + `file_size_mb` cached on model scan
    + `arch_max_ctx` from GGUF `context_length` field
    + Server validates `ctx_requested ≤ arch_max_ctx` before save

### API Response
`/admin/api/model-estimate` returns:

```json
{
  "tier": "good | cpu_offload | impossible | unknown",
  "can_save": true | false,
  "ngl_recommended": 28,
  "tier_message": "✓ Fits in VRAM: 3109 MB / 16311 MB",
  "system_ram_mb": 31694,
  "arch_max_ctx": 262144
}
```

### Fallback Models
Used by dry_load + watchdog:

```python
FALLBACK_MODELS = {
    "chat":       "gemma-4-E2B-it-Q4_0",
    "reasoning":  "gemma-4-E4B-it-Q4_0",
    "multimodal": "Qwen3VL-8B-Instruct-Q4_K_M",
    "embedding":  "bge-m3-Q8_0",
}
```

### GGUF Fallback Reading
`_classify_model_fit()` and `model_vram_estimate()` in `app/routes/admin.py`: if model not in `gguf_models_cache`, reads `block_count` and `expert_count` directly from GGUF file via `gguf.GGUFReader`. Detects MTP via `{arch}.nextn_predict_layers`.

## VRAM Guards & Timeouts
`_wait_for_vram` (in `queue.py`)
Before any multimodal/SD/Video call, blocks until at least 6 GiB VRAM is free. Polls `nvidia-smi` every 2s, times out after 60s.

### Synchronous VRAM Polling (`_poll_vram`)
`_resolve_use_gpu()` and `ensure_vram_for_llm()` call `_poll_vram()` synchronously before reading `available_vram_mb`. After every `unload_llamacpp_model()`, a wait loop verifies VRAM is actually freed (up to 30s).

`ensure_vram_for_reasoning`
Unloads llama.cpp models and waits (up to 60s) for SD/Video to free VRAM before loading gemma-4-E4B (~4.8 GiB).

### VRAM Timeout Varies by Context
  - `ensure_vram_for()` (resource_manager.py) — 15-second wait
  - `_wait_for_vram()` (queue.py) — 30 seconds
  - `_wait_for_vram_full()` (queue.py) — 60 seconds

**If VRAM isn't freed within the timeout, the task returns an error instead of proceeding into OOM.**

## LTX-Video Unload Optimization (v8.8+)
Three-part fix in `resource_manager.py:unload_video_pipeline()`:

### A1 — Pre-flight Check
`GET /v1/vram_info` before HTTP unload. If `pipeline_loaded=false`, return `True` immediately — skips 3×POST + 8s×8 polling (~28s saved per call).

### A3 — 30s Result Cache
`_last_ltx_unload_at` timestamp prevents double-call within 30s (queue.py calls unload twice per image request: once explicitly, once via `ensure_vram_for`).

### A2 — Reachable Success Condition
Changed from `free_before + 3000` to `min(total - 1000, free_before + 3000)`. Original condition was unreachable when `free_before > total - 3000` (our case: 15229 > 16311 - 3000).

### A4 — Docker Restart on 3 Consecutive Timeouts
`_maybe_restart_ltx_video()` restarts `flai-ltxvideo` container via Docker socket (`POST /containers/flai-ltxvideo/restart`) after 3 consecutive `ReadTimeout` exceptions. Rate-limited to 1 restart per 5 minutes.

### Tests
`tests/test_resource_manager_ltx_unload.py` — 11 tests across 4 classes (Preflight, Cache, SuccessCondition, DockerRestart).

## LTX-Video Unconditional Restart
`_force_restart_ltx_video()` called after every video task without rate-limiting. Docker handles concurrent restart gracefully. Frees ~3 GB CUDA context from gunicorn worker.

## Video Cancel Checker
`_start_cancel_checker()` spawns a daemon thread that polls Redis every 2s for `task:cancel:{task_id}`. On detection:
1. Restarts `flai-ltxvideo` container via Docker socket (`POST /containers/flai-ltxvideo/restart`)
2. Container receives SIGTERM → CUDA freed
3. `requests.post()` in `generate_video()` gets ConnectionError
4. Finally block runs VRAM cleanup (`_cleanup_vram_after_task()`)
5. Returns `{"status": "cancelled", "error": "..."}` to client

For image gen/edit: only pre/post checks (tasks are fast, no mid-generation interrupt). `_is_task_cancelled()` checked before `generate_image_params()` and before `image._call_wrapper()` / `image.edit_image()`.

## Dead Torch Code Cleanup
All `torch.cuda.empty_cache()` and `torch.cuda.synchronize()` calls removed from `app/queue.py`, `app/resource_manager.py`, `modules/video.py` (~60 lines). `flai-web` has no CUDA context — all torch calls were silent no-ops. VRAM cleanup is managed entirely by llama-swap TTL + `_force_restart_ltx_video()`.

## Historical VRAM Problems & Solutions (v8.8+)
### Problems
  - **CUDA OOM on video generation:** `_wait_for_vram()` only polled nvidia-smi without checking if llama.cpp models were unloaded, leading to fragmented memory claims and OOM when loading Qwen3VL-8B (~5GiB).
  - **HTTP 502 on reasoning queries:** `ensure_vram_for_reasoning()` returned False on timeout but code continued execution, attempting to load gpt-oss-20b (~10GiB) into insufficient memory.
  - **Video OOM persisted:** After multimodal generates params (~5GB), LTX-Video pipeline (~8GB) tries to load while multimodal is still in VRAM → total exceeds 15.47GB GPU → OOM.
  - **Hardcoded VRAM constants:** All VRAM estimates were hardcoded (2500/5000/15000/2000 MB), causing 502 errors when model was changed or VRAM was fragmented.

### Solutions Implemented
  1. `_wait_for_vram_full()` **redesign:** Changed from `≥80% GPU threshold` to `video_needed + 3GB buffer` — the 80% threshold was impossible to meet on a 15GB GPU after unloading a 5GB multimodal model (max free was ~10GB, need 12.4GB).
  2. **Timeout increase 30→60s:** Both in queue.py and video.py; CUDA memory deallocation is async and needs more time.
  3. **No "proceeding anyway":** When VRAM wait times out, return error instead of proceeding into OOM.
  4. **Buffer increase +500→+3000MB:** In `_resolve_use_gpu()` for safety margin against CUDA fragmentation.
  5. **Dynamic VRAM estimation via GGUF metadata** (see above).
  6. **Real VRAM measurement & storage** (see above).
  7. **Admin panel displays measured vs estimated VRAM** (see above).
  8. **Per-model-type circuit breakers:** Separate CB for chat, reasoning, multimodal, embedding. One model's failures don't block another.
  9. **Retry for reasoning on 500/502:** LlamaSwapBackend now retries reasoning requests once on 500 or 502, with automatic model degradation on first failure.
  10. **Phantom measurement fix** (see above).
  11. **LTX-Video unload optimization** (see above).

## Video Frame Policy (v9.0)
Replaces v8.8 cap-only approach.
  - **Default: 240 frames** (10 sec @ 24 fps), full 768×512 landscape.
  - **Capped to 120 frames** at 512×512 ONLY when:
    + `total_vram_mb < 10000` (8/10 GB tier GPU), OR
    + `available_vram_mb < 6000` (12+ GB tier with fragmented VRAM after multimodal unload)
  - `prompts/{en,ru}/create_video.template`: JSON default `num_frames: 240`. Instruction text says "use 240 unless user asks for short/5 sec".
  - `modules/video.py:generate_video`: applies cap with logging (`"VRAM tier 8GB: capped..."` or `"VRAM soft-cap (available=X MB): reduced..."`).
  - `modules/multimodal.py`: warning threshold `weight > free * 10` (240 frames = 92 weight, 6000 MB free = no spurious warning; fires only for extreme requests like 1000+ frames at 4K).
  - `ltx_wrapper.py`: `num_frames_padded = ((nf - 2) // 8 + 1) * 8 + 1` — both 120→121 and 240→241 are padded by +1 frame.

## Monitoring Commands
```bash
watch -n 1 nvidia-smi          # Real-time VRAM tracking
docker logs flai-web --tail 50 | grep GPU  # Log GPU-related events
grep "RAG\|reasoning\|router" docker/logs/flai-web.log  # Debug RAG flow
docker logs flai-web --tail 100 | grep -E "watchdog|dry_load"  # Model protection events
curl -s "http://localhost:5000/admin/api/model-estimate?model=gemma-4-E2B-it-Q4_0.gguf&module=chat&ctx_size=8192" | jq '{tier, can_save, ngl_recommended, tier_message}'
```

## Configuration

When adding or changing environment variables in `app/config.py`, both `.env` and `.env.example` MUST be updated. `.env` contains real values; `.env.example` has placeholders and comments. Section order must match.