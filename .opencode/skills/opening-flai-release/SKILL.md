---
name: opening-flai-release
description: "Use when starting a new FLAI release line — opening vX.Y from the previous release branch, cutting the branch, renaming the version that the product prints, and creating the empty What's New section before any feature work starts."
---

# Opening a FLAI Release Line

## Overview

Opening is a **mechanical** step, not a content step. It cuts the branch and
renames the version the product prints; it never decides what the release
contains.

The reference implementation is commit `c67ac18` ("v12.6: version bump after
finalizing v12.5"): 23 files, no prose, no features. When this skill and that
commit disagree, the commit wins.

Handoff:

| Skill | Owns |
|---|---|
| `opening-flai-release` (this one) | branch cut, current-version markers, dated previous CHANGELOG section, new `Unreleased` section, empty What's New |
| `preparing-flai-release` | merging release branches, audit, filling What's New, release gate, final report |

## Inputs

Ask for two values; derive the rest and state them before touching anything.

| Input | Example | Derived |
|---|---|---|
| `SOURCE` — release branch to cut from | `v12.6` | `PREV=12.6` |
| `TARGET` — new release line | `v12.7` | `12.7`, `12.7.0`, TOC anchors `v127` |

Refuse when: the tree is dirty, `TARGET` exists locally or on any remote,
`TARGET` is not newer than `SOURCE`, or `SOURCE` does not exist. A release
line with a skipped minor (`v12.6` → `v13.0`) is a deliberate choice — confirm
it instead of guessing.

## Hard rules

- Print the full plan (file, line, before, after) and wait for an explicit
  yes. No branch, no file edit, no commit, no push, no tag without approval —
  rule 4 of `AGENTS.md` applies unchanged.
- **No blind search-and-replace.** `sed -i 's/12.6/12.7/'` corrupts
  `CUDA_VERSION_SD="12.6.3"` and every historical `(v12.6)` annotation.
  Edit the listed markers by hand and leave everything else to the report.
- The mandatory set is exactly what the reference commit touched. Anything
  else that still says `PREV` is **reported**, never silently rewritten.
- `What's New` is empty when the line opens. Filling it is
  `preparing-flai-release` Phase 4.
- English only in code, `AGENTS.md`, `CHANGELOG.md`, `README.md` and
  `docs/*.md`; Russian only in the `-ru` twins.
- Commit only when asked. The commit subject follows the existing convention:
  `vX.Y: version bump after finalizing vX.(Y-1)`.

## Phase 0 — Preflight (read-only)

```bash
git fetch --all --prune
git status --short                    # must be empty
git branch --show-current
git branch --list "$TARGET"           # must print nothing
git ls-remote --heads origin "$TARGET"
git log -1 --format='%ad %s' --date=short "$SOURCE"
```

Record the `SOURCE` head date: it becomes the date of the previous CHANGELOG
section.

## Phase 1 — Cut the branch

```bash
git checkout -b "$TARGET" "$SOURCE"    # approval required
```

Never cut from `master`/`main`, never from a feature branch, never rebase an
existing release branch. If `TARGET` already exists on `origin`, stop: the
line is already open.

## Phase 2 — Current-version markers

Every row is mandatory. "Where" is the only place the version is printed for
that surface.

| Surface | File | Form after the bump |
|---|---|---|
| Code constant | `app/config.py` | `APP_VERSION = "12.7"` |
| Package | `pyproject.toml` | `version = "12.7.0"` |
| Public API spec | `docs/openapi-v1.yaml` | `version: 12.7.0` |
| Agent constitution | `AGENTS.md` | `# AGENTS.md — FLAI v12.7` |
| Doc titles (4 pairs) | `docs/ARCHITECTURE`, `LOCALIZATION`, `RELEASE_GUIDE`, `VRAM_MANAGEMENT` (+ `-ru`) | `… — FLAI v12.7` / `… — ПЛИИ v12.7` |
| Deploy scripts | `deploy.sh`, `deploy-ru.sh` | 4 lines each: header, success message, two usage strings |
| README heading | `README.md`, `README-ru.md` | `## 🆕 What's New in v12.7` / `## 🆕 Что нового в v12.7` |
| README TOC | same files | label **and** anchor: `- […](#-whats-new-in-v127)` / `(#-что-нового-в-v127)` |
| Translations | `translations/{en,ru}/LC_MESSAGES/messages.po` | `"Project-Id-Version: FLAI 12.7\n"` |
| Branch pair | `AGENTS.md` rule 4 | `(e.g. v12.6, v12.7)` |
| Branch pair in the guide | `CONTRIBUTING.md`, `CONTRIBUTING-ru.md` | `(\`v12.6\`, \`v12.7\`, …)` — keep both |
| Branch examples | `CONTRIBUTING.md`, `CONTRIBUTING-ru.md` | `features/v12.7-…`, `fix/v12.7-…`, `git checkout v12.7`, PR against `v12.7` |

Notes:

- Doc titles: only documents whose first heading already carries a version.
  A document without one stays without one — do not add versions to titles.
- README TOC anchors are generated: `python3 scripts/update_toc.py` is
  idempotent and rewrites both files after the heading changes.
- Translations: `.po` and compiled `.mo` are both tracked. After editing the
  `Project-Id-Version` header run `pybabel compile -d translations` and stage
  both files. `msgid` comments that mention old versions are extraction
  history — leave them.
- `deploy*.sh` hardcode the version (they do not read `APP_VERSION`), which is
  why there are four occurrences per file.

## Phase 3 — CHANGELOG

Two edits, both in `CHANGELOG.md`:

1. If the previous section still reads `## [v12.6] — Unreleased`, replace it
   with `## [v12.6] — <date of the SOURCE head commit>`.
2. Insert above it: `## [v12.7] — Unreleased`, followed by no subsections.
   Subsection headings (`### Added`, `### Fixed`, …) are added when they have
   content, never empty.

The new section stays `Unreleased` for the whole cycle; `preparing-flai-release`
dates it at release time. A release line without a CHANGELOG section is not
open.

## Phase 4 — Empty What's New

In both READMEs: rename the heading, fix the TOC anchor, and **delete the
previous release's bullets**. Keep the `Full history: [CHANGELOG.md](CHANGELOG.md).`
line. An empty section directly under the heading is the correct state here.

Do not seed it with placeholder text, do not keep the old items "until there
are new ones", do not switch to a table (the bullet list is the format).

## Phase 5 — Verify and report

```bash
python3 .opencode/skills/opening-flai-release/scripts/check_version_markers.py \
  --version 12.7 --previous 12.6
python3 .opencode/skills/preparing-flai-release/scripts/check_doc_headings.py
python3 .opencode/skills/preparing-flai-release/scripts/check_translations.py
git diff --check
git status --short
```

The marker script exits `0` when every mandatory marker is correct and prints
the remaining occurrences of the previous version as `REVIEW` rows. Read every
row: historical annotations, CHANGELOG history, and unrelated numbers (CUDA)
are expected; a `REVIEW` row in a file that prints the current version is a
missed marker.

`pytest tests/test_app_version.py` proves the single-source rule and the
`pyproject` pairing — 6 tests, one file. Run it only with explicit permission
(`pgrep -c pytest` = 0, `free -h` ≥ 4 GiB), otherwise write `NOT VERIFIED`.

Report:

```text
Release line v12.7 — OPENED / BLOCKED

Cut from:      v12.6 (head <sha>, <date>)
Version:       12.7 / 12.7.0
Markers:       N/N correct
CHANGELOG:     v12.6 dated <date>, v12.7 Unreleased
What's New:    empty in both READMEs
Review rows:   <count> (classified: historical / CUDA / needs fix)
Not verified:  <what was not run and why>
```

## Never touch

| Looks like the version | What it actually is |
|---|---|
| `CUDA_VERSION_SD="12.6.3"` in `deploy*.sh` | a CUDA version that happens to share digits |
| `(v12.6)` in `app/**`, `tests/**`, `app/static/**` | when a feature appeared |
| `**Tasks (v12.6)**` and similar in `AGENTS.md`, `docs/*` | historical provenance |
| `## [v12.6]` and older in `CHANGELOG.md` | released history |
| `12.6 – 12.7` driver ranges in `README*`, `CHANGELOG.md` | CUDA requirement |
| `fix/v12.1-…` examples in `CONTRIBUTING*` | an older, still valid example |
| `graphify-out/**`, `build/**`, `venv/**` | generated or environment-local |

## Out of scope — report, do not fix

- Pre-existing stale literals (for example `Generated by FLAI v11.3` in
  `app/llama_swap_config.py`).
- Contradictions and staleness inside `docs/RELEASE_GUIDE.md` (it still
  describes a "Roadmap → Completed" section and a What's New table that the
  READMEs do not have).
- Comment-only version mentions in CSS/JS (for example a footer-height comment
  in `app/static/css/chat.css`) — flag them, let the user decide.

Collect them in one list at the end of the report; each is a separate request.

## Red flags

| Thought | Reality |
|---|---|
| "There is no `Unreleased` section, so I will not add one." | The convention is the opposite: the previous section gets dated, the new one opens as `Unreleased`. |
| "One `sed` is faster than this table." | It rewrites `CUDA_VERSION_SD="12.6.3"` and every historical `(v12.6)`. |
| "I will keep the old What's New items until new ones exist." | The section holds one release. It is emptied at the cut. |
| "I will date the new section today." | It stays `Unreleased` until release prep. |
| "Every remaining `12.6` is a miss." | Most are history, CUDA, or a driver range. Classify, do not rewrite. |
| "The guide says a table / a Completed section." | The guide is stale; the READMEs use bullets and have no such section. |
| "While I am here, I will fix that v11.3 literal." | Out of scope. Report it as its own request. |
| "Cutting the branch is trivial, I will skip approval." | Branch creation needs explicit approval, always. |
