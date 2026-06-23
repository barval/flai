# Release Guide — FLAI v9.0

This document describes the process of releasing a new version, updating READMEs, and maintaining documentation. Read it when preparing a new release.

## Release Documentation Process

When releasing a new version, update these files **in order**:

### 1. README.md and README-ru.md — "What's New" section

- Located in the Architecture section (under `### What's New in vX.X`)
- List only the **most impactful changes** from the new version
- Format: `| **Feature name** | Brief description |`
- Keep entries concise (1-2 lines per feature)

### 2. README.md and README-ru.md — "Roadmap → Completed" section

- Located at the bottom of the Roadmap section (under `### ✅ Completed`)
- Add **only the most significant new features, major changes, and critical bug fixes**
- Do NOT add minor fixes, test changes, or internal refactoring
- Format: `- **Feature name** — brief description`

### 3. AGENTS.md — version section

- Update the version title in `# AGENTS.md — FLAI vX.Y`
- Document technical details of new features (architecture, algorithms, parameters)
- Update the "Known issues" section — move fixed items to the version section
- Move historical details to `CHANGELOG.md` or `docs/` files

### 4. CHANGELOG.md

- Add a new section for the version
- Include all significant changes, bug fixes, and migration notes
- See `CHANGELOG.md` template below

### 5. Git tags

```bash
git tag vX.Y
git push origin vX.Y
```

## Version Update Checklist
When releasing a new version:

### 1. Update version number in:
  - `pyproject.toml`: `version = "X.Y.Z"`
  - `app/__init__.py`: update `flai_web_info{version="X.Y"}` to match the new version
  - `AGENTS.md`: version title `# AGENTS.md — FLAI vX.Y`
  - `README.md`: `### What's New in vX.Y`
  - `README-ru.md`: Russian equivalent section header

### 2. Update "What's New" section in both READMEs:
  - Add new features to the table
  - Remove features that are no longer new
  - Keep entries concise (1-2 lines per feature)

### 3. Update "Roadmap → Completed" section in both READMEs:
  - Add significant new features and major changes
  - Do NOT add minor fixes, test changes, or internal refactoring
  - Format: `- **Feature name** — brief description`

### 4. Update model list if models changed:
  - Add new models to benchmark table
  - Update VRAM requirements
  - Update performance numbers

### 5. Update deployment commands if profiles changed:
  - Add new `--profile` flags
  - Update example commands
  - Update description of each profile

### 6. Create git tag:
```bash
git tag vX.Y
git push origin vX.Y
```

## README Update Guide
When updating README.md and README-ru.md, follow the rules in `docs/LOCALIZATION.md`.

### README.md (English)

Sections to update:
  1. **Features list** (`### ✨ Features`) — add new features, remove deprecated ones
  2. **What's New table** (`### What's New in vX.X`) — update version number and add new features
  3. **Core Components table** (`### Core Components`) — add/remove services, update ports
  4. **Hardware Tiers** (``### Hardware Tiers`) — update VRAM/RAM requirements if changed
  5. **Model Benchmarks** (`### Model Benchmarks`) — add new models, update performance data
  6. **Quick Start commands** (`### Quick Start`) — update deploy.sh flags if profiles changed
  7. **Docker Compose profiles** — list all available profiles with descriptions
  8. **Configuration** section — update environment variables if added/changed
  9. **`.env` / `.env.example` sync** — if env vars were added/changed in `app/config.py`, ensure both `.env` and `.env.example` are updated. `.env` has real values; `.env.example` has placeholders and comments. Section order must match.

Format rules:
  - Use tables for structured data (components, benchmarks, tiers)
  - Use emoji prefixes for feature lists (consistent with existing style)
  - Keep descriptions concise (1-2 lines per feature)
  - Include version number in "What's New" header: ### What's New in vX.X

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

### Common Mistakes to Avoid
  - ❌ Don't add code changes to README updates — separate commits
  - ❌ Don't change section order without reason — maintain consistency
  - ❌ Don't remove existing features from lists — only add new ones
  - ❌ Don't translate code blocks or command examples
  - ❌ Don't change emoji prefixes — keep existing style
  - ✅ Always verify both READMEs have identical structure
  - ✅ Always test deploy.sh commands after updating

## CHANGELOG.md Template

```markdown
# Changelog

All notable changes to FLAI are documented in this file.

## [vX.Y] — YYYY-MM-DD

### ✨ New Features
- **Feature name** — brief description

### 🐛 Bug Fixes
- **Fix description** — what was broken and how it was fixed

### 🔧 Improvements
- **Improvement description**

### 📦 Dependencies
- Updated `package` from `X.Y` to `A.B`

### 🗑️ Removed
- Removed deprecated `feature_name`

### 🔒 Security
- Security fix description

### 📝 Migration Notes
- Any breaking changes or migration steps
```

### Documentation Philosophy
  - **AGENTS.md** — Constitution: commands, critical rules, hard constraints. Always read by AI agents.
  - **docs/ARCHITECTURE.md** — Deep technical architecture. Read when modifying core logic.
  - **docs/VRAM_MANAGEMENT.md** — VRAM rules, GPU queue, model protection. Read when touching GPU-related code.
  - **docs/LOCALIZATION.md** — i18n rules, Flask-Babel, README translations. Read when adding user-facing strings.
  - **docs/TESTING.md** — Testing infrastructure, fixtures, mocking. Read when writing tests.
  - **docs/RELEASE_GUIDE.md** — Release process, version bumps. Read when preparing a new version.
  - **CHANGELOG.md** — Historical changes, bug fixes, migration notes. Read when debugging or understanding why something works this way.

Keep each file focused on its topic. Avoid duplication. Cross-reference between files when needed.