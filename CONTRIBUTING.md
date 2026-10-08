# Contributing to FLAI

Contributions are welcome. FLAI is a self-hosted, fully local multimodal AI platform — see [README.md](README.md) for what it does and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how it is put together.

**Before you start:** read [AGENTS.md](AGENTS.md). It is the project's constitution — the critical rules, hard constraints and exact commands. Every rule there applies to your contribution.

## Where to work

Development happens on a numbered main branch (`v12.4`, `v12.5`, …), never on `master`. Branch naming:

- Feature: `features/v12.5-<short-description>`
- Fix: `fix/v12.5-<short-description>`

Do not commit to `master` — it only ever advances through `--no-ff` merges from a development branch, and only after review.

## Workflow

```bash
# 1. Fork and clone your fork
git clone git@github.com:<your-fork>/flai.git
cd flai

# 2. Install the full development dependencies
pip install -e ".[dev]"

# 3. Branch from the current main development branch
git checkout v12.5
git checkout -b features/v12.5-my-feature

# 4. Make your change, then verify it
ruff check .
mypy app/ modules/
pytest -m unit

# 5. Commit, push, open a Pull Request against v12.5
git commit -m "feat(module): short description of the change"
git push origin features/v12.5-my-feature
```

## Hard constraints

These are not negotiable and will be rejected in review:

1. **GPU tasks run strictly sequentially.** Any GPU task on the fast *or* slow worker must acquire `_gpu_lock`. Two GPU tasks must never run concurrently. VRAM is cleaned unconditionally between every GPU task — no predictive logic.
2. **No hardcoded routing.** Request classification goes through the LLM router. Keyword lists and Python-level query filters that bypass the router are forbidden.
3. **Error messages shown to users start with `⚠️ `.** Build them through `_build_error_response()`; never return a raw `str(e)`.
4. **No autonomous commits.** Ask before `git add`, `git commit`, `git push`, `git tag` or `git revert`.
5. **Back up the database before any work on it** — schema changes, manual SQL, migrations, imports. No exceptions.
6. **Every user-facing string is translated.** Wrap it in `_()`, and keep both `translations/{en,ru}/LC_MESSAGES/messages.po` in sync.
7. **New dependencies must have an open-source license** (MIT, BSD, Apache 2.0, MPL). Proprietary and copyleft (GPL/AGPL) dependencies are prohibited — verify the license before adding.
8. **Translation parity for docs.** Every user- or admin-facing `docs/X.md` needs a `docs/X-ru.md` twin with the same section structure, in the same commit.

## Code style

- `ruff check .` (line length 120) and `mypy app/ modules/` must pass.
- All CSS lives in `app/static/css/`, all JS in `app/static/js/`. No inline styles, no CDN links.
- No debug prints, commented-out blocks, dead code, or unused files.
- Comments and log messages in English.
- Prefer small focused units over large ones. If a file grows past a few hundred lines, that is usually a signal to split it.

## Adding a feature

1. Check [docs/ROADMAP.md](docs/ROADMAP.md) — the feature may already be planned.
2. Read the relevant `docs/` file from the Documentation Map in `AGENTS.md`.
3. Write the test first, make it fail, then make it pass.
4. Update the docs: the `docs/` file that owns the topic, and the README only if the change alters what FLAI can do.
5. Add a `CHANGELOG.md` entry if the change is user-visible.

## Adding a model

Model files, download commands and licenses are documented in [docs/MODELS.md](docs/MODELS.md). Record the size, the license and the download instructions. Verify the license is open source.

## Adding a configuration key

Environment variables are documented in [docs/CONFIGURATION.md](docs/CONFIGURATION.md). When you add, remove or change a key, update **both** `.env` and `.env.example` — the first with real values, the second with placeholders. Section order and structure must match between the two files. Never commit secrets from `.env` into `.env.example`.

## Tests

Full guidance lives in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). In short:

```bash
pytest                  # everything
pytest -m unit          # fast tests only
pytest -m "not slow"
pytest --cov=app --cov=modules --cov-report=html
```

Run one suite at a time on a memory-constrained machine — a pytest run builds a fresh Flask app per test, and two concurrent suites will exhaust host RAM.

## License

Contributions are accepted under the [MIT License](LICENSE).