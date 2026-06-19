# Changelog

All notable changes to FLAI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
- **Background task errors leaking to users** — `_process_fact_extraction()` and `_process_fact_merge()` were not wrapped in try/except. Any exception (network error, parse error, LLM failure) was caught by `_process_single_task` and published as an SSE error event, causing `⚠️ Ошибка: ...` messages to appear in the user's chat after a successful response.
- **Phantom ⚡ after chat response** — `fact_extraction_task` (enqueued on slow worker after every chat response >20 chars) had the same `session_id` as the main task. `get_user_requests_status()` reported it as `processing`, causing the lightning bolt to reappear. Background tasks are now excluded from queue status display.
- **Negative queue counter** — `fact_extraction_task` was added directly to slow queue via `redis.rpush()` without `add_request()`, but `_process_single_task()` always called `_decrement_user_queue_count()` in its `finally` block. After N responses, `user_counts[user_id]` drifted to -N (e.g. `-9`), causing displays like `📊 -9/0`. Fixed by skipping decrement for background tasks and adding `max(0, ...)` guard in `get_user_queue_counts()`.
- **flash-attn SIGABRT on Blackwell GPUs** — `--flash-attn on` with `--n-gpu-layers > 0` (partial offloading) caused SIGABRT on Blackwell sm_120 GPUs (llama.cpp build 9294). Flash-attn is now disabled when partial offloading (ngl > 0). Effective ngl is computed before the flash-attn logic to ensure correct decision.
- **500 errors causing unnecessary model degradation** — `LlamaSwapBackend.call()` only retried on 502 but not 500, causing transient llama-swap errors to trigger `degrade_and_reload()` on the first failure. Now retries on both 500 and 502 (`response.status_code in (500, 502)`).
- **Gunicorn workers 2→1** — `threading.Lock()` (`_gpu_lock`) only works within a single process. With 2 gunicorn gevent workers, GPU tasks could run concurrently across processes. Reduced to 1 worker. Single worker is optimal for GPU-bound workloads.
- **Preload after fresh YAML** — `generate_and_write()` now accepts `include_preload` parameter. On initial startup: `include_preload=False` (prevents crash from stale on-disk YAML). On admin reload and dry_load: `include_preload=True` (preloads model before first request).
- **Merge watcher queue flooding** — SLM merge watcher now checks `llen(slow_queue_key) > len(user_ids)` before enqueueing, preventing redundant merge tasks from flooding the queue during prolonged idle.
- **Generic reasoning output visible to user** — Some models (gemma-4-E2B, gpt-oss-20b) output chain-of-thought as plain text without `<thinking>` tags. `_strip_generic_reasoning()` detects common reasoning markers (e.g. "Analyze Persona:", "Final Answer Generation:") and strips everything up to the actual answer. Applied to ALL model types server-side (queue.py, llamacpp_client.py) and client-side (events.js). Chat and reasoning templates (RU/EN) updated with explicit instruction: "Write ONLY the final answer. Do NOT write reasoning, analysis, thinking steps."

### 🔧 Improvements

- **Stronger style instructions** — All 5 response styles (`neutral`, `academic`, `professional`, `friendly`, `funny`) now include explicit prohibitions (`НЕ используй...`) to improve style adherence by small local models.
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