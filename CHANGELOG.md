# Changelog

All notable changes to FLAI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### ✨ Features

- **Blackwell-aware model deployment** — Chat and reasoning models are now auto-selected based on GPU architecture. MXFP4 variants (`Qwen3-4B-Instruct-2507-MXFP4_MOE`, `gpt-oss-20b-mxfp4`) are downloaded/configured on Blackwell GPUs (RTX 5060+, native FP4 tensor cores). Standard Q4_0/Q4_K_M quantizations are used on other NVIDIA GPUs (Ampere, Ada Lovelace) for optimal prefill performance. Detection via `nvidia-smi --query-gpu=name`. Covers: deploy scripts (`deploy.sh`, `deploy-ru.sh`), seed DB (`app/database.py`), fallback models (`app/tasks/dry_load.py`), and `is_blackwell_gpu()` utility (`app/utils.py`).

### 🐛 Bug Fixes

- **`<|channel|>` thinking tokens: `analysis` stripped, `commentary` unwrapped** — gpt-oss-20b uses two channel types: `analysis<|message|>` for reasoning (strip entirely) and `commentary<|message|>` for the actual answer (unwrap — keep content, remove tags). Previous fix stripped all `<|channel|>` blocks, killing search results that gpt-oss-20b delivers via `commentary`. Now `_THINK_OPEN_RE` is specific to `analysis<|message|>` only; `_strip_thinking_tags()` unwraps `commentary` blocks and strips malformed `<|channel|>...` without `<|message|>`. `<|channel|>` is intentionally **not** a stop token — gpt-oss-20b always starts generation with it.
- **`_strip_thinking_tags()` dangerous regex in `app/queue.py`** — Second regex `r"<\|channel\|>analysis<\|message\|>[\s\S]*$"` deleted the entire response when gpt-oss-20b produced an `analysis` block without a closing `<|end|>` tag (happens with large search contexts, ~600+ tokens). Replaced with `r"<\|channel\|>analysis<\|message\|>(?:[\s\S]*?<\|end\|>)?"` — non-greedy, optional `<|end|>`, never deletes beyond the tag boundary.
- **Buﬀer ﬂush in `app/llamacpp_client.py`** — `_process_stream_chunk` deferred output when `_thinking_active=True`, discarding tokens that arrived after the `analysis` block ended but before the `commentary` block began. Now performs unconditional buffer flush when `_thinking_active` transitions from `True` to `False`.
- **Router URL→SEARCH misclassification** — Router (Qwen3-4B) classified queries containing URLs (e.g. `example.com`, `github.com`) as `[-RAG-]` instead of `[-SEARCH-]`. Added explicit URL examples in SEARCH category and exclusion in RAG category in `prompts/{ru,en}/base_text.template`.
- **Ruff false positives on JS files** — `ruff check .` reported 3177 errors in `events.js` (ruff cannot parse modern JS). Excluded `"*.js"` from ruff. Replaced non-ASCII dashes (`─`, `—`) with ASCII `-` in `events.js`.
- **`_strip_generic_reasoning()` returning empty string** — When a small model (Qwen3-4B) produced reasoning markers followed by a short answer (<100 chars), the function returned `""` instead of the answer. Now returns original `text` if parsed answer is empty, never discarding valid responses.
- **Tool invocation instruction without tools** — System prompt contained "используй инструменты когда это необходимо — не придумывай ответ, лучше вызови инструмент" but tools were `null`, causing models to cite skills instead of answering directly. Both the tool invocation instruction and `time_calc` examples are now conditional on `include_tools=True`.
- **`expose_tools` parameter** — Separates tool definitions from system prompt content. Category 1 (fast chat) now uses `include_tools=False` (short prompt without time/tool sections) + `expose_tools=True` (model can still call tools). Fixes date calculations like "когда ближайшее 16 число выпадает на четверг?" returning wrong year.
- **Emoji missing from stage messages** — Transition from hardcoded emoji in `STAGE_LABELS` (v9.0) to i18n via `t()` lost emoji in all 9 stage messages (⏳ 🔍 🎬 🎨 ✏️ 🧠 📹). Restored in `.po` files for both RU and EN locales. Added bind mount `./translations:/app/translations` in `docker-compose.gpu.yml` so `.mo` changes persist through container restarts.
- **Router system message missing 3 categories** — System message in `modules/base.py:369` only listed SIMPLE/REASONING/IMAGE/VIDEO/CAMERA but omitted SEARCH, RAG, and REMEMBER. Router (Qwen3-4B) frequently misclassified search queries (e.g., "Найди описание и цену смартфона Poco X8 Pro в DNS") as category 1, causing chat model to hallucinate answers instead of performing web search. Updated to explicitly list all 8 categories from `base_text.template`.
- **`_strip_generic_reasoning()` false positives on Russian text** — `Нужно`, `Следует`, `Необходимо`, `Анализ`, `Проверка`, `Формулировка`, `Идентификация`, `Рассмотрение`, `План`, `Комментарий`, `Коррекция`, `Уточнение` were regex patterns matching ordinary Russian words in informative responses, causing the function to strip actual answer content. Removed overbroad Russian patterns, kept only clearly reasoning-specific markers (`Мне нужно`, `Пользователь спросил`, `Генерация финального ответа`, etc.). Raised threshold from `>=1` to `>=2` markers to match JS client. Removed redundant server-side `_strip_generic_reasoning()` call from `queue.py` — filtering now happens only at display time.
- **Reasoning model hallucinates web search results** — When web search was used ([-SEARCH-] route), the search context (2115 chars of real results) was correctly injected into `conversation_history` via `_get_context_for_model()`, but: (1) the heading always said "Найденная информация из документов" regardless of source — the reasoning model treated search results as irrelevant document context; (2) `reasoning.template` had a dead `{rag_context}` placeholder that was always empty string; (3) no anti-hallucination instruction told the model to base its answer on provided context. Result: model generated 13K tokens of completely fabricated news ("Siri AI Core", "PaLM-3", "Medical-Data-X") while ignoring real search data. Fixed: web search context now gets a prominent heading ("Результаты поиска в интернете — ИСПОЛЬЗУЙ ТОЛЬКО ЭТИ ДАННЫЕ"), anti-hallucination rules added to `reasoning.template` (ru + en), dead `{rag_context}` removed from template and `format_prompt()` calls.
- **TTS reads markdown formatting aloud** — `**bold**` and `*italic*` in chat responses were sent verbatim to Piper TTS, causing asterisks to be spoken ("звезда-звезда"). Added `clean_markdown_for_tts()` in `app/utils.py` that strips bold, italic, code, links, images, headings, quotes, lists, HTML tags, and orphaned `**` fragments (from sentence-split at `.` inside URLs like `**НН.РУ**`). Called in `modules/tts.py:synthesize()` before sending to Piper. Added `modules/tts.py` bind mount to `docker-compose.gpu.yml`. 22 tests in `TestCleanMarkdownForTTS`.
- **SLM stores model responses as user facts** — `slm_rules.py` extracted facts from the model's response (`response`) instead of the user's query (`query`). Pattern `я работаю` matched the model's "Как я могу помочь?" (contains "я" + verb), and `score >= 0.40` threshold was too low for typical assistant outputs. `slm_import.py` imported `role='assistant'` messages into SLM, causing 289 junk facts for user valery (~5 real). Fixed: extract from `query` not `response`, added `_MODEL_RESPONSE_PATTERNS` filter for typical assistant outputs, raised threshold to `0.50`, removed bonus for "я+verb". Import now skips `role='assistant'` (except direct user quotes). Merge pipeline got step 0: `_is_model_response()` cleanup. Added `flask cleanup-slm --user <id>` CLI command.
- **SLM facts not deleted when session deleted** — Three bugs: (1) `fact["id"]` in sessions.py used wrong key (SLM facts use `fact_id`), causing `KeyError` silently caught; (2) SLM daemon does not store `session_id` in `atomic_facts` (all empty), so filtering by metadata was a no-op; (3) `clear_history` did not touch SLM at all. Fixed: new `_delete_session_facts_from_slm()` matches facts against session's user message contents (exact match or substring), works regardless of metadata. Both `delete_session` and `clear_history` now clean up SLM facts. Added bind mounts for `cli.py`, `slm_rules.py`, `slm_import.py`, `slm_merge.py`, `routes/sessions.py`.

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
- **Double ⚠️ error prefix** — Server `_build_error_response()` and client `events.js` both prepended "⚠️ " to error messages, resulting in "⚠️ ⚠️ ..." display. Removed redundant prefix from client-side `onError()` and `onStreamCancelled()` handlers.
- **llama-swap errors not translated** — Raw llama-swap error messages (e.g. "model not found", "context size exceeded") were displayed to users in English. Added `_translate_llama_swap_error()` in `llamacpp_client.py` with RU/EN translations for common errors.
- **Database migration .gguf suffix** — `database.py:327` migration for chat model name update didn't include `.gguf` suffix variant, causing old Gemma model names to persist in some deployments.
- **Chat auto-scroll broken** — `_isLoadingMessages` flag added to `chat-messages.js` to prevent N competing async scroll callbacks. `isNearBottom()` threshold increased to 200px. `scrollToBottom()` simplified. `overflow-anchor: none` added to chat container CSS.
- **Cancel button missing for image/video** — Cancel button (■) only appeared for streaming text tasks. Added cancel support for image generation, image editing, and video generation tasks. Backend: `_is_task_cancelled()` polls Redis `EXISTS task:cancel:{id}`. Frontend: `_showHeaderCancelButton()` in `onTaskProgress()`, `onVideoStep()`, `onImageStep()`. Video cancel uses background checker thread + container restart to interrupt mid-generation.

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