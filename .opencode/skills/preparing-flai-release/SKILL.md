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
python3 .opencode/skills/preparing-flai-release/scripts/check_catalog_drift.py
python3 .opencode/skills/preparing-flai-release/scripts/check_api_docs.py
python3 .opencode/skills/preparing-flai-release/scripts/check_doc_headings.py
python3 .opencode/skills/preparing-flai-release/scripts/check_readme_toc.py
python3 .opencode/skills/preparing-flai-release/scripts/check_secrets.py
```

The branch, translation, catalog-drift, API, TOC and secrets scripts accept `--json`; run any script with `--help` for details. A non-zero exit is evidence, not a reason to bypass the check.

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
REL=vX.Y
PREV=vX.(Y-1)
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

Scan **tracked files only**. A plain `grep -r` also walks git-ignored trees — the nested
third-party service clone under `services/room-snapshot-api/room-snapshot-api/` (which holds
Russian camera configs and its own `README-ru.md`) — and buries the real findings in noise.

```bash
git ls-files 'app/*.py' 'modules/*.py' 'tests/*.py' 'services/*' 'prompts/*' \
  | xargs -r grep -lP '[\x{0400}-\x{04FF}]' \
  | grep -vE '^(prompts/ru/|services/kokoro/ru_g2p\.py|services/superlocalmemory/)'

git ls-files 'AGENTS.md' 'CHANGELOG.md' 'README.md' 'docs/*' \
  | grep -vE '(README-ru\.md|.*-ru\.md)$' \
  | xargs -r grep -lP '[\x{0400}-\x{04FF}]'
```

**A file-level hit is not a finding.** Nearly every module that processes Russian text matches
a file, and the string literals are the data it processes. Narrow the scan to the two places a
defect can hide — comments and docstrings:

```bash
python3 - <<'PY'
import ast, io, tokenize
from pathlib import Path
CYR = lambda s: any('\u0400' <= ch <= '\u04FF' for ch in s)
for p in Path("app").rglob("*.py"):
    src = p.read_text(encoding="utf-8", errors="replace")
    if not CYR(src):
        continue
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc and CYR(doc):
                print(f"{p}:{getattr(node, 'lineno', 0)} docstring {getattr(node, 'name', '<module>')}")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT and CYR(tok.string):
            print(f"{p}:{tok.start[0]} comment {tok.string.strip()[:80]}")
PY
```

Repeat the same idea for JS/CSS/HTML comments (`//`, `/* */`, `<!-- -->`). In `app/templates`
and `app/static/js` the only allowed Cyrillic is the language-switcher labels (`base.html`,
`header.js`), and in `docs/demo/*-en.html` the switcher labels are legitimate too — the English
demo page carries «Русский» because a language switcher must name each language natively.

Classify every remaining hit:

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

The script checks both `.po` catalogs for missing ids, empty and fuzzy entries, and verifies that each compiled `.mo` matches its source. It compares the two catalogs with **each other**, so a string that exists in the code and in neither catalog passes it — and that string reaches the user in English. Run the drift check as well:

```bash
python3 .opencode/skills/preparing-flai-release/scripts/check_catalog_drift.py
```

It extracts the tree with the same keys `babel.cfg` is read with and reports three sections:

- `untranslated` — present in the code, absent from a catalog. **A user-facing string here is a defect**: it renders in the source language.
- `obsolete` — in a catalog, no longer in the code. Informational; it is what `pybabel update` marks obsolete.
- `structural` — raw-format defects Babel parses without complaining: two `msgstr` lines in one entry (Babel silently keeps the last), two `msgid` lines in one entry, a duplicated entry, or an unescaped `"` inside a message. These come from editing a catalog block by hand — the compiled `.mo` then disagrees with what the file appears to say. Treat every one as a blocker.

`check_translations.py` exits `0` on all three of these; that is why the drift check exists.

Then, when a change is needed:

```bash
pybabel extract -F babel.cfg -k _tr -k ApiImageRejectedError -k ApiTaskError \
  -o translations/messages.pot .
pybabel update -i translations/messages.pot -d translations
# Review every fuzzy block; fill real translations in en and ru.
pybabel compile -d translations
git status --short translations
```

- `babel.cfg` lists **files only**; the extraction keywords come from the `-k` flags. Babel's defaults already cover `_`, `gettext` and `N_`; `_tr` and the localized error classes are not defaults, so a new wrapper must be added to both the command here and the one in `AGENTS.md`. A wrapper missing from the flags produces a string that never reaches a catalog — exactly what `check_catalog_drift.py` reports.
- No empty `msgstr`, no fuzzy entries, both `en` and `ru`.
- One `msgstr` per entry, no duplicated entry, quotes inside a message escaped as `\"`; `check_catalog_drift.py` exits `0`.
- Never replace a catalog block with a text splice: a hand-written `msgid`/`msgstr` pair drops the blank line before it, leaving two entries glued together, and leaves the old `msgstr` behind. Let `pybabel update` write the entry, or re-run the drift check immediately after a manual edit.
- `.po` and `.mo` are committed.

### Secrets and real data in published artefacts

A release publishes everything that is committed: demo pages and snapshots are downloadable by
anyone. **No real account, password hash, API key, token, Tavily/E-mail identifier, session dump,
backup file or private path may reach a tracked file** — the demonstration and the fixtures included.

```bash
python3 .opencode/skills/preparing-flai-release/scripts/check_secrets.py
```

The script reads tracked files. A real camera configuration (`services/room-snapshot-api/room-snapshot-api/config/cameras.conf`) is therefore invisible to it — and must stay that way: that whole tree is a nested clone listed in `.gitignore`. Confirm the ignore rule rather than the absence of the hit:

```bash
git check-ignore -v services/room-snapshot-api/room-snapshot-api   # must print a .gitignore rule
git ls-files services/room-snapshot-api | wc -l                    # only the wrapper files, never the clone
```

Classify every hit: a placeholder (`flai_xxxxxxxx…`, `tvly-abcdefgh12345678`) is expected in docs and
fixtures; anything else is a blocker. For a real hit:

1. Remove it from the working tree and rebuild the artefact (a demo page is generated, never edited).
2. Fix the pipeline that produced it, and add a guard test so it cannot come back.
3. **Rotate the credential** (password, key, token) — a public artefact may already have been copied,
   so removing the file is not enough.
4. Assess git history (`git log -S <value> --all`). If the value is in pushed commits, report the
   branches and commits to the user and rewrite history only with explicit approval (a mirror backup
   first, then `git filter-repo --replace-text`, then a force-push to every remote).

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

Neither test covers parameters, payloads or response schemas, and neither looks at the prose. Run the third guard:

```bash
python3 .opencode/skills/preparing-flai-release/scripts/check_api_docs.py
python3 .opencode/skills/preparing-flai-release/scripts/check_api_docs.py --live http://localhost:5000
```

It checks what the tests do not:

- `info.version` equals `APP_VERSION` plus `.0` (a spec stamped with the previous release is a release blocker);
- every operation carries `responses`, `operationId`, a summary or description, and declares each `{path}` parameter (references to `#/components/...` are resolved);
- a body-bearing method (`POST`/`PUT`/`PATCH`) either has `requestBody` or is marked `x-no-body: true`, so "no body" is a decision rather than an omission;
- every spec path is described in `docs/API.md` **and** `docs/API-ru.md`, and every templated `/v1` path those documents mention exists in the spec;
- `docs/openapi.yaml` still carries its staleness disclaimer;
- with `--live`, the served `/v1/openapi.json` matches the file and `/v1/docs` answers `200`.

What still needs a human eye is the content of a changed endpoint:

- request body and query parameters, their types and required flags;
- response schema and status codes, including the localized `⚠️ ` error bodies;
- the endpoint table, authentication notes and examples in `docs/API.md` and `docs/API-ru.md`.

The internal web/admin API (`/api/...`) has **no** spec and no drift guard: `docs/openapi.yaml` is a v8.2 snapshot whose header says it is not maintained, not served and not checked against the code, and the code in `app/routes/` is the reference. Do not update that file to look current — keep the disclaimer intact, confirm nothing links to it as the current reference, and report the internal API as `NOT VERIFIED` unless the release changed a `/api/...` route and you reviewed it by hand.

## Phase 3 — Documentation and version

Start from the Documentation Map in `AGENTS.md`. A major feature without an owner doc is a release blocker unless the user accepts a README-only entry.

For every changed feature, update `docs/X.md` and `docs/X-ru.md` in the same commit; same heading count and order.

Update:

- `AGENTS.md`: version title, architecture summary, critical rules, known issues; move history out.
- `README.md` and `README-ru.md`: Features, What's New, Core Components, Hardware Tiers, Benchmarks, Quick Start, Compose profiles, Configuration, Contents links.
- `CHANGELOG.md`: `## [vX.Y] — YYYY-MM-DD`, all significant changes, migration notes. While the release is being prepared the heading reads `## [vX.Y] — Unreleased`; replacing `Unreleased` with the release date is part of the version bump, not of writing the entries.
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

### Demo pages

The four published demo pages (`docs/demo/{chat,admin}-{ru,en}.html`) are generated from the
committed snapshot. They go stale whenever the interface changes, and a stale page ships an
interface that no longer exists. They are a release gate:

```bash
python -m demo.build --check        # names the stale pages; a clean tree prints nothing to fix
python -m demo.build                # rebuild presentation only, from the existing snapshot
pytest tests/test_demo_pages.py     # drift guard: committed bytes == freshly built bytes
```

The rebuild is presentation-only. `demo/snapshot/` and `demo/media/` **must come out untouched** —
check `git status --short demo/` and `git diff --stat docs/demo/` before committing. Re-capturing
content (`python -m demo.capture`: requests, answers, images, videos) happens **only on an explicit
user request**; a UI change never re-captures.

Environment sync (`.env.example` deliberately comments out optional keys, so the comparison must
include them; a plain `^KEY=` grep reports every optional key as missing):

```bash
diff -u <(grep -oE '^#? *[A-Z0-9_]+=' .env.example | sed -E 's/^#? *//' | sort -u) \
         <(grep -oE '^[A-Z0-9_]+=' .env | sort -u)
```

## Phase 4 — README "What's New"

Replace the section; do not append. It contains only this release.

Source of truth: the `CHANGELOG.md` section for `vX.Y`, `git log "$PREV..$REL"`, and the merged branches.

**What's New is a release digest, not a changelog.** The CHANGELOG records every change; the README
answers one question for someone upgrading: *what will change how I work, and what would surprise
me?* A bullet that answers neither belongs in the CHANGELOG only.

Test each candidate bullet before writing it:

- Would a user upgrading from `$PREV` do something differently, or be surprised by the new behaviour?
- Is it a whole feature or a whole subsystem, not one step inside one?

If both answers are not yes, leave it out.

A **removal or refusal** is a What's New item even when the feature behind it is small: someone who
used the removed path has a silently broken workflow, whatever a "major changes" reading of the list
would call it.

Never in the README:

- minor or one-line bug fixes;
- typo corrections, wording and translation tweaks;
- tests, internal refactoring, dead-code removal;
- routine dependency and version bumps;
- documentation-only edits;
- catch-all bullets ("also fixed", "various improvements").

Rules:

- 3–6 bullets: the most significant changes, additions and fixes of **this** release only.
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
| Catalog drift and PO structure | `check_catalog_drift.py` | PASS / FAIL |
| No secrets or real data | `check_secrets.py` + classification | PASS / FAIL |
| Dead code | `ruff --select F...`, diff review | PASS / FAIL |
| Docs parity | `check_doc_headings.py`, `.env` diff | PASS / FAIL |
| Demo pages current | `python -m demo.build --check`, `tests/test_demo_pages.py` | PASS / FAIL |
| Roadmap reconciled | delivered items removed, next cycle listed | PASS / FAIL |
| README TOC resolves | `check_readme_toc.py` | PASS / FAIL |
| What's New | manual review: significant changes only, no minor fixes or typos | PASS / FAIL |
| Focused tests | `pytest ...` | PASS / NOT VERIFIED |
| API docs (paths) | `tests/test_api_docs.py` | PASS / FAIL |
| API contract (methods) | `tests/test_api_inventory.py` | PASS / FAIL |
| API prose, spec structure, served spec | `check_api_docs.py` (with `--live` when the instance runs) | PASS / FAIL |
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
| "`check_translations.py` passed, so the translations are complete." | It compares the catalogs with each other. A string that is in the code and in neither catalog passes it and ships in English. Run `check_catalog_drift.py`. |
| "I'll just replace this `.po` block with the new text." | A text splice leaves the old `msgstr` behind and glues the previous entry on. Let `pybabel update` write the entry, or re-run the drift check. |
| "The demo is regenerated when someone asks." | `python -m demo.build --check` names every stale page; a stale page publishes an interface that no longer exists. |
| "`.mo` will be built at deploy." | `.mo` is tracked; compile and commit it. |
| "The docs are close enough." | Heading parity and `.env` key parity are mechanical checks. |
| "A Russian quote in an English document is fine." | It is a defect. Describe the meaning in English. |
| "The `/v1` tests passed, so the API docs are current." | They compare paths and method sets. The prose, the spec structure and the served Swagger page are separate — run `check_api_docs.py`. |
| "`docs/openapi.yaml` is out of date, I will regenerate it." | It is a v8.2 snapshot with a disclaimer that says so; the internal API has no spec and the code is the reference. Leave the disclaimer alone. |
| "Full pytest is the only proof." | Narrow runs are the default; the full suite needs explicit approval and a RAM budget. |
| "I'll merge master into the release branch." | No. Promote the release branch to master after approval. |
| "I'll delete old dead code while I'm here." | Report it; delete only with approval, including old code. |
