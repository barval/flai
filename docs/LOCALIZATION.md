# Localization (i18n) — FLAI v9.0

This document describes the localization system, Flask-Babel usage, and rules for translating READMEs. Read it when adding user-facing strings or updating README files.

## Core Rules

- All **code comments** and **log messages** must be in English.
- All **user-facing messages** (UI, notifications, errors) must use the selected user language (i18n).
- Always keep translation files (`messages.po`) up-to-date and complete.
- For Russian, the file `deploy-ru.sh` is the only place where Russian comments are allowed.
- **Every** user-facing string MUST be wrapped in `_()` / `self._()` / `gettext()`. Raw `str(e)` must NEVER be returned to the user.
- **When adding or modifying error messages**, ALWAYS verify that corresponding translation keys exist in both `translations/en/LC_MESSAGES/messages.po` and `translations/ru/LC_MESSAGES/messages.po`.

## Flask-Babel 4.0.0 — `_tr()` Format Strings

**CRITICAL**: Flask-Babel `gettext()` uses `%`-formatting (`string % variables`), NOT `str.format()`.

Passing `{status}` kwargs directly to `gettext()` silently returns the unformatted string.

### Correct Pattern

Always call `gettext(key)` without kwargs, then apply `result.format(**kwargs)` manually.

**Examples**:
- `app/llamacpp_client.py:28`
- `app/mixins.py:9`

```python
# ❌ WRONG — silently returns unformatted string
message = _tr("Error: {status}", status=status)

# ✅ CORRECT
message = _tr("Error: {status}").format(status=status)
```

## pybabel Extraction
Always use `-k _tr` flag when extracting, since `_tr` is a custom keyword not recognized by default:

```bash
pybabel extract -F babel.cfg -k _tr -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations
pybabel compile -d translations  # after editing .po files
```

## Translation System (v9.0+)
Compiled `.mo` files are baked into the Docker image via `RUN pybabel compile -d translations` in the Dockerfile. For live updates without image rebuild, `docker-compose.gpu.yml` mounts `./translations:/app/translations` as a bind volume. After editing `.po` files, run `pybabel compile -d translations` on the host, then `docker exec flai-web kill -HUP 1` to reload gunicorn. All site features work in both Russian and English profiles.

## Error Message Prefix
All error messages displayed to users MUST start with `"⚠️ "`.

`_build_error_response()` adds this prefix automatically. However, error strings from `call_llamacpp()` (e.g., `"GPU memory unavailable"`, `"HTTP error 500"`) were returned as plain strings through `process_reasoning()` and `rag.generate_answer()` — ending up in `_save_and_respond()` without the `"⚠️ "` prefix.

Fix: Added `_is_llm_error_string()` helper and routed detected errors through `_build_error_response()` in all affected code paths:
  - `_process_reasoning_request`
  - `_process_text_task`
  - `_process_text_task_stream`
  - RAG answer handling in both `_process_text_task` and `_process_text_task_stream`

## README Translation Rules

### README.md (English)

Sections to update:

  1. **Features list** (`### ✨ Features`) — add new features, remove deprecated ones
  2. **What's New table** (`### What's New in vX.X`) — update version number and add new features
  3. **Core Components table** (`### Core Components`) — add/remove services, update ports
  4. **Hardware Tiers** (`### Hardware Tiers`) — update VRAM/RAM requirements if changed
  5. **Model Benchmarks** (`### Model Benchmarks`) — add new models, update performance data
  6. **Quick Start commands** (`### Quick Start`) — update deploy.sh flags if profiles changed
  7. **Docker Compose profiles** — list all available profiles with descriptions
  8. **Configuration** section — update environment variables if added/changed

Format rules:

  - Use tables for structured data (components, benchmarks, tiers)
  - Use emoji prefixes for feature lists (consistent with existing style)
  - Keep descriptions concise (1-2 lines per feature)
  - Include version number in "What's New" header: `### What's New in vX.X`

### README-ru.md (Russian)

Translation rules:

  - Translate ALL new content from README.md to Russian
  - Keep technical terms in English (Docker, GPU, VRAM, llama.cpp, etc.)
  - Keep code blocks and command examples in English
  - Translate section headers, feature descriptions, and explanatory text
  - Maintain identical structure and formatting as README.md

Sections that MUST be translated:

  - Feature list (all bullet points)
  - What's New table (Feature column translated, Notes column translated)
  - Core Components table (Component and Purpose columns translated)
  - Hardware Tiers (Feature column translated)
  - Quick Start instructions (descriptions, not commands)
  - All explanatory text and notes

Sections that stay in English:

  - Code blocks (```bash ... ```)
  - Command examples (`./deploy.sh --download-models`)
  - Technical parameters (ports, model names, file paths)
  - Badge text (already in English)
  - GitHub links and URLs

### Common Translation Mistakes
  - ❌ Don't translate code blocks or command examples
  - ❌ Don't change emoji prefixes — keep existing style
  - ❌ Don't change section order without reason — maintain consistency
  - ✅ Always verify both READMEs have identical structure