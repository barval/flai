---
name: deploying-flai
description: "Use when an AI agent must install, update or verify a FLAI deployment on a server the user names — a fresh clone, an update of an existing installation, or a check that a running install is healthy."
---

# Deploying FLAI

## What this skill is for

The user points an agent at this repository and a server, and the agent deploys
FLAI there: inspects the host, asks only what it cannot detect, runs the supported
script, downloads the models, verifies the result and reports. This is the
procedure to follow — `deploy.sh` stays the source of truth for flags and defaults,
and its `--help` must be read before running it.

The target is a self-hosted installation on the user's own hardware: one machine,
Docker, no cloud on the processing path.

## Hard rules

- **Only the host named in this request.** A host listed in an SSH config, a panel
  or a previous conversation is not permission. If no host is named, ask.
- **Approval before** writing `.env`, pulling models (tens of gigabytes), opening a
  port to the network, changing an existing installation, deleting a file, or
  running the test suite.
- **Back up the database before any update of an existing installation** and check
  the dump is not empty:
  `docker exec flai-postgres pg_dump -U flai -d flai > backups/pre-update_$(date +%F_%H-%M-%S).sql`
- **Never print a secret.** `SECRET_KEY`, API keys and tokens stay on the host; show
  a mask when reporting.
- **`deploy.sh` creates `.env` only when it is absent.** An existing `.env` is never
  rewritten — treat it as the user's configuration, not as a generated file.
- Do not invent compose commands: the script resolves the file and the profile list.
  When a command is needed outside the script, take it from the script's own output.
- AMD and Intel are **experimental** (never run by this project). Detect the vendor
  and confirm with the user before choosing one.

## Phase 0 — Recon (read-only)

Run on the target host and report a table, not prose:

```bash
uname -a; cat /etc/os-release | head -2
docker --version; docker compose version; docker info 2>/dev/null | grep -E "Runtimes|Storage"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null
free -h; df -h /; nproc
ss -ltn 2>/dev/null | grep -E ':5000|:8080' || true
ls -la .env 2>/dev/null; docker ps --format '{{.Names}}\t{{.Status}}' | head
```

What the answers decide:

| Finding | Consequence |
|---|---|
| NVIDIA with `nvidia-smi` and the nvidia runtime | GPU mode, `docker-compose.gpu.yml` |
| No NVIDIA, but `amdgpu`/`vulkaninfo` | Vulkan (experimental) — confirm first |
| No GPU at all | `--cpu`, slower, media downscales automatically |
| Free RAM under ~16 GB | A smaller profile; SLM and Kokoro are the first to drop |
| Free disk under ~40 GB | Models will not fit; ask before starting |
| `.env` exists | An update, not an install — back up first |
| Containers already running | An update; never `down -v` |

## Phase 1 — Ask, then act

Ask in one message, with the detected default first. Do not ask what recon answered.

| Decision | Default | What to say |
|---|---|---|
| CPU-only or GPU | detected | «Нашёл RTX 5060 Ti, 16 ГБ — ставлю GPU-режим, если не против» |
| Optional services | none | image generation (+~7 GB disk, VRAM), video LTX or Wan (Wan needs ≥24 GB VRAM), RAG/Qdrant, SLM memory, web search, crawler, voice (Whisper + Piper or Kokoro ~6 GB RAM) |
| Models now or later | now | ~20–30 GB download and 20–60 min; without them the interface starts but nothing answers |
| Exposure | `localhost:5000` | LAN (`0.0.0.0`) or a reverse proxy with TLS — ask which, and never expose 5000 to the internet unwrapped |
| Admin password | none | you will set it with `flask admin-password`; ask for a password or let the user set it later |
| Data location | repo `./data` | mounted volume; say where documents and uploads will live |

## Phase 2 — Install

```bash
git clone <repo> flai && cd flai
git checkout <release branch or tag>       # never a feature branch for a deployment
./deploy.sh --download-models [flags] --help first
```

Flags map to profiles one-to-one (`--with-image-gen`, `--with-rag`, `--with-slm`,
`--with-search`, `--with-crawler`, `--with-voice-piper|--with-voice-kokoro`,
`--with-video|--with-video-wan`, `--cpu`, `--gpu-nvidia|--gpu-amd|--gpu-intel`).
`--with-video` and `--with-video-wan` are mutually exclusive; the voice backends
are too.

The script checks prerequisites, creates directories, writes `.env` from
`.env.example` with a generated `SECRET_KEY`, resolves the compose file and
profiles, generates the llama-swap config, downloads models and builds.

## Phase 3 — Models

Sizes come from `./deploy.sh --help`; quote them instead of estimating. Check the
disk **before** the download and say what remains after it. A model download can be
deferred: the stack starts, `/health` is green, and the first message then takes a
model load. Tell the user which of the two they are choosing.

## Phase 4 — Verify (always, even when the script says "complete")

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep flai
curl -s http://localhost:5000/health
docker exec flai-web flask admin-password '<password>'
```

| Check | Pass looks like |
|---|---|
| Containers | every `flai-*` container `Up (healthy)` |
| `/health` | `status: ok`, and `services.database`, `redis`, `llamacpp` are `ok` |
| Memory service | `services.slm` is `ok` when SLM is deployed — `daemon unreachable` means the daemon died, not that the install succeeded |
| Login page | `GET /` answers `200` |
| Admin | the password command returns success; it is set, not printed |
| First answer | one real message through the UI or `/v1/chat/completions` returns text |

Report each row as PASS or NOT VERIFIED. A green container says nothing about the
code inside it; only the checks above do.

## Phase 5 — Update an existing installation

**The supported update is the install command itself, with the same flags**:

```bash
docker exec flai-postgres pg_dump -U flai -d flai > backups/pre-update_$(date +%F_%H-%M-%S).sql
git checkout <release branch or tag> && git pull --ff-only
./deploy.sh --download-models <the same flags as the install>
```

It keeps the existing `.env`, runs `docker compose build` for **every service in those
profiles**, then `up -d` — which is what rebuilds `web` and `services/*`, whose code
lives in an image. `docker compose up -d` alone restarts the old images and is not an
update. The script prints the compose command it used; take the file and profiles from
that output, never from memory.

Without the script, the equivalent sequence — note `build` with no service list:

```bash
docker compose -f <file> config >/dev/null        # catches a broken merge before it starts
docker compose -f <file> build
docker compose -f <file> up -d
```

Then check that what runs is what the install command names:

```bash
docker compose -f <file> ps --services            # compare with the profiles in use
```

A service missing from that list means `.env` advertises a feature that is not running
(search without `searxng`, memory without `superlocalmemory`, no TTS container).

- `./app`, `./modules`, `./prompts` are bind-mounted: a restart is enough.
- **`services/*` is baked into images.** A fixed bug there keeps running until the
  service is rebuilt. `superlocalmemory` is the measured case — check which
  services the release touched and rebuild exactly those.
- **A changed default does not reach the user.** Their `.env` keeps the value from
  the first install and beats the code fallback. Say which defaults changed since
  their last install and offer both ways: edit `.env`, or save the value in Admin →
  Settings (the panel value overrides `.env`, and the panel marks each field's
  source and the value this version ships).
- Never overwrite `.env` from `.env.example`: it carries their secrets and choices.
  Since v13.3 a run without the original flags does not switch optional services off
  either — it keeps them and says so (`--strict-stack` opts into switching them off).
  Repeat the flags anyway: they decide which profiles are built and started.
- After the update, repeat Phase 4.

## Phase 6 — Rollback

Stop, then return the checkout to the previous tag or branch, rebuild the same
services and `up -d`. Restore the database only on an explicit instruction, and
only from a dump whose size was checked. Report what was lost between the backup
and the rollback.

## Phase 7 — When something fails

```bash
docker logs flai-web --tail 100
docker logs flai-llamaswap --tail 50
docker compose -f <file> config | head -40
nvidia-smi
```

- Port 5000 busy: stop the other process or change the mapping; do not kill a
  service the user did not mention.
- `unhealthy` on `flai-slm`: the daemon inside the container can be down while the
  wrapper answers `200` — check `services.slm` in `/health`, then
  `docker exec flai-slm slm serve status`.
- Out of VRAM: smaller model set or CPU mode; the router and the queue will not fix
  a card that cannot hold the model.
- More: `docs/TROUBLESHOOTING.md` and `docs/VRAM_MANAGEMENT.md`.

## Report

```text
FLAI deployment — <host>

Mode:        GPU (NVIDIA, 16 GB) / CPU
Profiles:    image gen, RAG, search
Version:     v13.3
URL:         http://<host>:5000
Models:      downloaded (N GB) / deferred
Health:      status ok · database ok · redis ok · llamacpp ok · slm ok
Checks:      containers PASS · login PASS · first answer PASS
Not done:    <what was skipped and why>
Next:        <admin password, reverse proxy, backups>
```
