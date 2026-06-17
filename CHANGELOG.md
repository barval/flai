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

- **Chat temperature too low for style sensitivity** — `_process_chat_with_tools()` hardcoded `temperature=0.1` for all chat model calls, making style instructions ineffective. Removed hardcode; chat model now uses DB-configured temperature (default 0.7). Fact extraction retains `temperature=0.1` for deterministic JSON output.
- **STYLE_INSTRUCTIONS duplicated in 3 modules** — Identical style maps existed in `modules/base.py`, `modules/rag.py`, and `modules/multimodal.py`. Removed duplicates from `rag.py` and `multimodal.py`; both now import `STYLE_INSTRUCTIONS` from `base.py`.
- **Context window overflow risk with SLM** — `_get_context_for_model()` reserved a hardcoded 490 tokens (7 × 70) for SLM facts, but actual fact sizes could exceed this, stealing space from conversation history. Now fetches SLM facts first, measures real token cost, then calculates history budget with the actual SLM size subtracted.
- **Translation system** — Removed `.mo` volume mounts that were overriding correct compiled translations. Docker now properly compiles all translations at build time.
- **Queue position** — Removed `pendingRequestIds` race guard from `chat-queue.js`. Queue positions now come exclusively from server data.
- **Multi-tab session** — Client now sends `session_id` in request body. Server validates (UUID v4 + user ownership). Fixes race conditions between browser tabs.
- **Error message prefix** — All error messages now start with `"⚠️ "`. Added `_is_llm_error_string()` helper.
- **RAG architecture** — RAG on fast worker now does ONLY search (`rag.search()`). Answer generation via reasoning model happens EXCLUSIVELY on slow worker. Prevents GPU contention with LTX-Video.
- **userdb.py schema mismatch** — `delete_user()` used `user_id = user["id"]` (INTEGER) against TEXT columns. Fixed to `user_id = login` (TEXT).
- **test_backups.py** — Added `Babel(flask_app)` to fixture. Fixed `shutil.copytree FileExistsError` via `dirs_exist_ok=True`.
- **mypy app/utils.py** — 19 → 0 errors via `_gguf_scalar()` helper.
- **`call_llamacpp()` missing `temperature` parameter** — `BaseModule.call_llamacpp()` and `LlamaClient.call()` did not propagate `temperature` to `chat()`. Fact extraction, SLM extract, and SLM merge functions passed `temperature=0.1` which raised `TypeError`. Added parameter to both methods with proper passthrough.
- **`KeyError('text')` in fact extraction** — `_process_fact_extraction()` accessed `f["text"]` on SLM facts that may lack the key. Changed to `f.get("text", "")` with filtering. Same fix applied in `slm_extract.py`.
- **Background task errors leaking to users** — `_process_fact_extraction()` and `_process_fact_merge()` were not wrapped in try/except. Any exception (network error, parse error, LLM failure) was caught by `_process_single_task` and published as an SSE error event, causing `⚠️ Ошибка: ...` messages to appear in the user's chat after a successful response.
- **Phantom ⚡ after chat response** — `fact_extraction_task` (enqueued on slow worker after every chat response >20 chars) had the same `session_id` as the main task. `get_user_requests_status()` reported it as `processing`, causing the lightning bolt to reappear. Background tasks are now excluded from queue status display.
- **Negative queue counter** — `fact_extraction_task` was added directly to slow queue via `redis.rpush()` without `add_request()`, but `_process_single_task()` always called `_decrement_user_queue_count()` in its `finally` block. After N responses, `user_counts[user_id]` drifted to -N (e.g. `-9`), causing displays like `📊 -9/0`. Fixed by skipping decrement for background tasks and adding `max(0, ...)` guard in `get_user_queue_counts()`.

### 🔧 Improvements

- **Stronger style instructions** — All 5 response styles (`neutral`, `academic`, `professional`, `friendly`, `funny`) now include explicit prohibitions (`НЕ используй...`) to improve style adherence by small local models.
- **Context logging enhanced** — `_get_context_for_model()` now logs SLM fact count and token cost separately: `Context loaded: 12 history msgs, 5 SLM facts (387 tokens), 8421 tokens (25.6% of 32768)`.
- **Dead code cleanup** — Removed `get_gguf_model_info()`, `find_gguf_file()`, `chunk_text_by_sentences()`, `clear_camera_rooms()`, `get_database_type()`, `is_postgresql()`, `close_db()`, `generate_chat_response_stream()`. Removed CSS classes `.capabilities`, `.capability`.
- **Chat video export** — `saveChatAsHTML()` collects `<video>` elements, fetches video files, converts to base64. Video rendered as `<video controls preload="metadata">`.
- **Dead torch code cleanup** — Removed all `torch.cuda.empty_cache()` and `torch.cuda.synchronize()` calls (~60 lines). `flai-web` has no CUDA context.
- **Skills list centralized** — All skills/capabilities text extracted to `prompts/{ru,en}/skills.txt` as single source of truth. `format_prompt()` auto-injects `{skills_section}` when the template contains the placeholder. Previously skills were duplicated (and inconsistent) across `chat.template`, `reasoning.template`, `rag.template`, `image_text.template`, and inline Python code in `queue.py`. Now 10 files (8 templates + 2 master copies) always show the same 10 skills.
- **`_process_chat_with_tools()` skills from master file** — Inline system prompt in `_process_chat_with_tools()` now loads skills via `_load_skills_section()` instead of a hardcoded list that could drift from the templates.

### 📦 Dependencies

- Added `pymorphy3>=2.0.6` for Russian morphological analysis.

### 📝 Migration Notes

- `camera_rooms` table added. Migration `migrate_name_forms()` regenerates existing room forms with pymorphy3 on startup.
- `model_vram_estimates` table PK changed from `(module)` to `(module, model_name)`. Idempotent migration in `init_db()`.

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