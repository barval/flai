---
name: preparing-flai-release
description: "Use when preparing, auditing, or signing off a FLAI version release (vX.Y) — before merging release branches, bumping the version, updating READMEs/docs, or declaring the release ready."
---

# Preparing a FLAI Release

## Overview

A release is a gate, not a cleanup sprint. Every claim in the release report must be backed by a command run on the current tree. Memory of "what we changed" is not evidence.

Two modes:

- **Audit** (default, read-only): produce findings and a release report.
- **Apply** (only after explicit approval): make the approved changes, then re-run the gate.

Never silently fix, merge, commit, push, tag, or touch the database.

## Helper scripts

Run these from the repository root. They are read-only and exit non-zero when they find a problem: `0` clean, `1` findings, `2` usage or git error.

```bash
python3 .opencode/skills/preparing-flai-release/scripts/check_release_branches.py --release vX.Y --previous vX.(Y-1)
python3 .opencode/skills/preparing-flai-release/scripts/check_translations.py
python3 .opencode/skills/preparing-flai-release/scripts/check_doc_headings.py
python3 .opencode/skills/preparing-flai-release/scripts/check_readme_toc.py
```

The branch, translation and TOC scripts accept `--json`; run any script with `--help` for details. A non-zero exit is evidence, not a reason to bypass the check.

## Hard rules

- Work on the designated `vX.Y` release branch. Never commit directly to `master`; merge to `master`/`main` only with approval.
- No file change, branch creation, merge, commit, push, tag, or DB work without explicit approval.
- Merge branches one at a time: ask before each merge, report the commits and conflicts, and stop on the first conflict. Do not invent resolutions.
- Back up the database before any DB work (schema check, data repair, migration).
- Tests: the narrow set below is mandatory. CI is the next gate. A full local `pytest` run happens only with explicit user permission. Check `pgrep -c pytest` and `free -h` first; never run two suites at once or background one.
- Code, comments, logs, tests, `AGENTS.md`, `CHANGELOG.md`, `README.md`, and `docs/*.md` are English only. Russian is allowed only as the documented exceptions: localized content and Russian-language data.
- User-facing text is localized; every error shown to a user starts with `⚠️ `.
- `APP_VERSION` in `app/config.py` is the only code source of the version; `pyproject.toml` carries the full `X.Y.Z`.
- `.po` and compiled `.mo` are both tracked.
- Report `PASS` only with command output; otherwise write `NOT VERIFIED`.

## Phase 0 — Freeze and inventory

```bash
REL=v12.6
PREV=v12.5
git fetch --all --prune
git status --short
git branch --show-current
git log --oneline --decorate -20
```

Build the release manifest. Do not trust branch names; count commits.

```bash
python3 .opencode/skills/preparing-flai-release/scripts/check_release_branches.py \
  --release "$REL" --previous "$PREV"
```

Record: branch, commits, merged status, owner, conflicts. The release range is `$PREV..$REL`, not `$PREV..HEAD`.

## Phase 1 — Merge every release branch

- If the local release branch does not exist: `git checkout -b "$REL" "$PREV"` (approval).
- If it exists, confirm it is up to date with `origin/$REL`; fast-forward only, never rebase a shared release branch.
- Ask before each merge. Merge with `git merge --no-ff "$branch"`, report the commit count and result, then continue.
- A branch that is an ancestor of `$REL` is already merged. A branch that contains `$REL` can fast-forward, but prefer `--no-ff` for a traceable release merge unless the user chooses otherwise.
- Do not merge `master`/`main` into the release branch.
- After all merges, `check_release_branches.py` must exit `0`; the output must contain no `UNMERGED` rows.
- Push the release branch only with approval.

## Phase 2 — Code, language, translations, dead code

### Static checks

```bash
ruff check .
python -m compileall -q app modules
mypy app/ modules/        # advisory; CI runs it with `|| true`
git diff --check
```

### English-only scan

```bash
grep -rIlP '[\x{0400}-\x{04FF}]' app modules tests services prompts \
  --exclude-dir=__pycache__ \
  | grep -vE '^(prompts/ru/|services/kokoro/ru_g2p\.py|services/superlocalmemory/)'

grep -rIlP '[\x{0400}-\x{04FF}]' AGENTS.md CHANGELOG.md README.md docs \
  | grep -vE '(README-ru\.md|.*-ru\.md)$'
```

Classify every hit:

- Allowed: `prompts/ru/**`, `translations/ru/**`, `README-ru.md`, `docs/*-ru.md`, `LICENSE-ru`, `CONTRIBUTING-ru.md`, `deploy-ru.sh`.
- Allowed as data: Russian regexes, word lists, month/weekday names, stop words, SLM/Kokoro/search fixtures and assertions that exercise Russian behavior.
- The only Cyrillic allowed in `app/` is the language-switcher labels in `base.html` and `header.js`.
- A Russian comment, docstring, log message or test name in English code is a defect.
- A Russian quote in an English document is a defect; describe the meaning in English instead.

### User-facing strings

- Inspect `git diff "$PREV..$REL" -- '*.py' '*.html' '*.js'`.
- Every new user-facing string is wrapped in `_()`, `_tr()` or `gettext()`.
- Errors go through `_build_error_response()` or start with `⚠️ `; raw `str(e)` is never returned.
- New JS `TRANSLATIONS` keys exist in both languages and in the fallback layer.

### Translations

```bash
python3 .opencode/skills/preparing-flai-release/scripts/check_translations.py
```

The script checks both `.po` catalogs for missing ids, empty and fuzzy entries, and verifies that each compiled `.mo` matches its source. Then, when a change is needed:

```bash
pybabel extract -F babel.cfg -k _tr -k ApiImageRejectedError -k ApiTaskError \
  -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations
# Review every fuzzy block; fill real translations in en and ru.
pybabel compile -d translations
git status --short translations
```

- No empty `msgstr`, no fuzzy entries, both `en` and `ru`.
- `.po` and `.mo` are committed.

### Dead code and unused symbols

```bash
ruff check --select F401,F811,F821,F841 .
grep -rnE '\b(print|breakpoint)\(' app modules
grep -rnE 'pdb\.set_trace|debugger;|console\.log' app modules app/static/js
```

- Inspect the release diff for commented-out blocks, obsolete TODOs, unused routes, templates, CSS and JS.
- Ruff finds unused imports and locals, not unused module-level functions or orphan assets. For each new module, confirm an import; for each removed feature, search for references.
- Report pre-existing dead code separately. Delete it only with approval, including old code.

### Tests

```bash
pytest tests/test_app_version.py tests/test_api_docs.py \
  tests/test_api_inventory.py tests/test_js_signatures.py \
  tests/test_translations.py
```

- Add the test files for changed areas.
- The full suite is a CI/merge-gate decision. Locally run only the narrow set; state what was not run.

### API docs

`tests/test_api_docs.py` is the path-level drift guard: every registered `/v1` route must appear in `docs/openapi-v1.yaml`, and every documented path must exist. It compares path templates only. It does not look at HTTP methods, request parameters, request or response bodies, or error codes, so a spec with the wrong verb and an outdated payload still passes.

`tests/test_api_inventory.py` is the method-level contract guard: it enumerates every in-scope `/v1` endpoint with its exact method set, so a removed route, a rename or a dropped method fails the build. Its `EXPECTED_ENDPOINTS` table mirrors the endpoint table in `docs/API.md`; add a new endpoint there and in the test in the same commit.

Neither test covers parameters, payloads or response schemas. Review these by hand for every changed endpoint:

- HTTP methods and path parameters in `docs/openapi-v1.yaml`;
- request body and query parameters, their types and required flags;
- response schema and status codes, including the localized `⚠️ ` error bodies;
- the endpoint table, authentication notes and examples in `docs/API.md` and `docs/API-ru.md`.

The internal web/admin API (`/api/...`) has no drift guard. `docs/openapi.yaml` is an older standalone document and no test compares it with the code. Check the endpoints the release touched against it by hand, and report the internal API as `NOT VERIFIED` when you did not.

## Phase 3 — Documentation and version

Start from the Documentation Map in `AGENTS.md`. A major feature without an owner doc is a release blocker unless the user accepts a README-only entry.

For every changed feature, update `docs/X.md` and `docs/X-ru.md` in the same commit; same heading count and order.

Update:

- `AGENTS.md`: version title, architecture summary, critical rules, known issues; move history out.
- `README.md` and `README-ru.md`: Features, What's New, Core Components, Hardware Tiers, Benchmarks, Quick Start, Compose profiles, Configuration, Contents links.
- `CHANGELOG.md`: `## [vX.Y] — YYYY-MM-DD`, all significant changes, migration notes.
- `docs/ROADMAP.md` and `-ru`: remove delivered items, rewrite the partially done ones (see below).
- `docs/RELEASE_GUIDE.md` and `-ru` if the process itself changed.
- `docs/CONFIGURATION.md` and `-ru`, `.env`, `.env.example` for every environment change; key order and sections match.
- `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT.md`, `docs/MODELS.md`, `docs/BENCHMARKS.md`, `docs/MONITORING.md` as touched.

### ROADMAP reconciliation

`docs/ROADMAP.md` and `docs/ROADMAP-ru.md` hold planned and in-progress work only; delivered work belongs to `CHANGELOG.md`, which is why the roadmap has no "Completed" section. Reconcile it against the release being prepared:

- An item this release delivered leaves `## 🔄 In Progress` (or `## 📅 Planned`) entirely. Its record is the CHANGELOG entry written in this same phase.
- An item the release advanced but did not finish stays, rewritten to the remaining work: delete the shipped sub-bullets instead of leaving them listed as if still open.
- Add what the next cycle will actually work on, in priority order, taken from the user's direction for the next release — not from a wish list.
- Both twins change together; heading count and order stay identical.

Judge delivery by evidence, not memory: a roadmap item is delivered only when the `CHANGELOG.md` entry for this release names it and the code shows it. Otherwise it stays.

```bash
git log --oneline "$PREV..$REL"
grep -n '^## ' docs/ROADMAP.md
```

Version bump:

- `pyproject.toml`: `version = "X.Y.Z"`.
- `app/config.py`: `APP_VERSION = "X.Y"`.
- `AGENTS.md`: `# AGENTS.md — FLAI vX.Y`.
- `README.md` and `README-ru.md`: What's New header.
- `translations/{en,ru}/LC_MESSAGES/messages.po`: `Project-Id-Version` only.
- `deploy.sh`, `deploy-ru.sh`: headers, success messages, usage text.
- `docs/*.md`: titles and change notes.

Verify:

```bash
pytest tests/test_app_version.py
grep -n '^version' pyproject.toml
grep -n 'APP_VERSION' app/config.py
grep -n 'FLAI v' AGENTS.md | head
grep -n "What's New in" README.md README-ru.md
grep -n 'Project-Id-Version' translations/*/LC_MESSAGES/messages.po
```

Documentation heading parity and README links:

```bash
python3 .opencode/skills/preparing-flai-release/scripts/check_doc_headings.py
python3 .opencode/skills/preparing-flai-release/scripts/check_readme_toc.py
```

The TOC check compares every entry with the `##` headings and recomputes the
anchor the renderer will generate. Anchors are never written by hand: run
`python3 scripts/update_toc.py` after any heading change (`docs/DEVELOPMENT.md`,
"README table of contents").

Environment sync:

```bash
diff -u <(grep -oE '^[A-Z0-9_]+=' .env.example) <(grep -oE '^[A-Z0-9_]+=' .env)
```

## Phase 4 — README "What's New"

Replace the section; do not append. It contains only this release.

Source of truth: the `CHANGELOG.md` section for `vX.Y`, `git log "$PREV..$REL"`, and the merged branches.

Include only:

- major new features;
- major behavior or architecture changes;
- critical bug fixes.

Exclude minor fixes, typos, tests, internal refactoring, routine dependency bumps and documentation-only edits.

Rules:

- 3–6 bullets, each traceable to a release commit or CHANGELOG entry.
- Each bullet links to its owning document.
- Mirror the same order and meaning in `README-ru.md`; technical terms stay English.
- Keep the current bullet-list format used by the READMEs. Do not switch to a table. If `RELEASE_GUIDE.md` disagrees, fix the guide.
- No "Unreleased" in the release READMEs.

## Phase 5 — Release gate and report

Check and record evidence:

| Check | Command | Result |
|---|---|---|
| All release branches merged | `check_release_branches.py` | PASS / FAIL |
| Version consistent | `pytest tests/test_app_version.py` | PASS / NOT VERIFIED |
| English-only | Cyrillic scan + classification | PASS / FAIL |
| Translations complete | `check_translations.py`, `pybabel compile` | PASS / FAIL |
| Dead code | `ruff --select F...`, diff review | PASS / FAIL |
| Docs parity | `check_doc_headings.py`, `.env` diff | PASS / FAIL |
| Roadmap reconciled | delivered items removed, next cycle listed | PASS / FAIL |
| README TOC resolves | `check_readme_toc.py` | PASS / FAIL |
| What's New | manual traceability review | PASS / FAIL |
| Focused tests | `pytest ...` | PASS / NOT VERIFIED |
| API docs (paths) | `tests/test_api_docs.py` | PASS / FAIL |
| API contract (methods) | `tests/test_api_inventory.py` | PASS / FAIL |
| Tree clean | `git status --short`, `git diff --check` | PASS / FAIL |

Final report:

```text
Release vX.Y — READY / BLOCKED

Merged branches: ...
Unmerged branches: ...
Version: ...
Tests run: ...
Not run: ...
Known risks: ...
```

Commit and push only with approval. Commit messages are English. Push to `origin` (gitea), then verify the upstream SHA. Do not create a release tag unless the user asks for one.

## Red flags

| Thought | Reality |
|---|---|
| "The feature branch is probably merged." | A branch can sit hundreds of commits outside the release branch. Use `check_release_branches.py`. |
| "Ruff is clean, so there is no dead code." | Ruff misses unused module-level functions and orphan assets. Inspect the diff. |
| "This small fix belongs in What's New." | Minor fixes and typos are excluded by definition. |
| "`.mo` will be built at deploy." | `.mo` is tracked; compile and commit it. |
| "The docs are close enough." | Heading parity and `.env` key parity are mechanical checks. |
| "A Russian quote in an English document is fine." | It is a defect. Describe the meaning in English. |
| "Full pytest is the only proof." | Narrow runs are the default; the full suite needs explicit approval and a RAM budget. |
| "I'll merge master into the release branch." | No. Promote the release branch to master after approval. |
| "I'll delete old dead code while I'm here." | Report it; delete only with approval, including old code. |
