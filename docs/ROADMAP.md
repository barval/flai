# Roadmap

Planned and in-progress work for FLAI. Recent delivered work is recorded in [../CHANGELOG.md](../CHANGELOG.md); what each subsystem does *today* is documented in the file that owns it (see the Documentation Map in [../AGENTS.md](../AGENTS.md)).

This list is not a commitment. Items move when their priority changes, and an item may be dropped if it turns out not to be the right approach.

## 🔄 In Progress

- *Nothing right now.* The queue prioritisation that sat here shipped in v13.4 — see `CHANGELOG.md`.

## 📅 Planned

- **Make mypy actually check the project** — the type check is advisory in CI (`mypy … || true`)
  and, as configured, it does not run at all: `python_version = "3.11"` meets numpy 2.5's stub,
  which uses the PEP 695 `type X = …` syntax added in 3.12, and mypy aborts on that file
  (`errors prevented further checking`) before it reaches `app/`. Measured with
  `--python-version 3.12`: **204 errors in 40 files**, plus 71 notes — 60 of them
  `annotation-unchecked` (bodies with no annotations, i.e. blind spots). Of the 204 errors:
  51 `union-attr`, 44 `no-any-return`, 39 `arg-type`, 21 `attr-defined`, 13 `operator`, and
  36 the rest. A reading pass over the
  candidates found **three places where the type is a real risk, not noise**: `app/tasks/store.py:update_task`
  is annotated `-> dict` and returns `None` when the row is gone; a bytes `stderr` slice is
  formatted into a log line in `app/routes/backups.py`; `modules/cam.py` calls `.rstrip()` on
  `camera_api_url`, which is `None` until `initialize()`. Everything else is `Any` from untyped
  dependencies, Flask extension attributes mypy cannot know (`app.modules`, `app.request_queue`),
  or annotations that disagree with the code without changing behaviour. Next: choose the target
  (3.12 for a check that runs, with `python3.11 -m compileall` keeping the image-version guard;
  or 3.11 with `follow_imports=skip`, 181 findings and no dependency types), declare the Flask
  extensions with a `Protocol`, fix the three real candidates, then remove `|| true` from CI and
  ratchet the count down file by file.

- **Editing documents the assistant wrote** — the tool writes and appends text today, and can
  emit `.docx`/`.xlsx`/`.pdf`, but it cannot change what it wrote. Editing is scoped to
  **generated documents only**: an upload is never modified in place, because its bytes may be
  shared (the blob layer keeps one copy per hash) and a recipient's copy must not change under
  them. A request to fix an uploaded file is answered by writing a **new document built from
  it**, under a different name, and editing that one. Planned in four steps, in value order:
  (1) replace the content of a generated text document, with a version row and a rollback;
  (2) replace a generated `.docx`/`.xlsx` from a full new body, warning that a spreadsheet
  loses charts and images on save; (3) targeted edits (one paragraph, one cell) only if a real
  need appears; (4) a generated PDF is re-rendered from its stored source text, never patched —
  a PDF is a drawing, not text. Every step sets the document `stale` so search cannot serve the
  old body.

- **An adaptive lead for frequent actions** — the lead is a constant: ten minutes, capped at
  half the interval for a repeating action (v13.5). That is a rule of thumb, not a measurement.
  The next step is to lead by what the item's own runs actually cost — the median duration of its
  last N completed tasks plus a margin — so a check that takes 40 seconds is not queued five
  minutes early and one that takes four minutes is not queued too late. Needs: a per-item
  duration history (the `tasks` rows already carry `started_at`/`completed_at`), a small
  aggregate in the tick, and a measurement on the reference card before choosing the margin.

- **Alternative LLM inference engines (research)** — evaluate Strata, PolyStrata and other local
  engines as a possible complement to llama.cpp + llama-swap: OpenAI-compatible serving on Linux and
  in containers, the low-VRAM throughput they claim (8 GB cards), multimodal and embedding support,
  model formats, MIT licensing and maintenance risk. Deliverable: a short report with measurements
  on the reference hardware and a go/no-go for the serving layer.

- **Long-term memory with facts in it** — v13.4 found and fixed the reason typed facts never
  appeared: the parser required a `confidence` field the rules never asked for, so a correct fact
  the model had extracted was dropped four times out of six. With that fixed, the remaining
  obstacle is material, and it is now measured: the installation's 116 user messages contain no
  statement of a durable personal fact — the history is test and demo traffic (weather, camera
  checks, media prompts). Next: a conversation that actually says something about the user, or a
  labelled set built from one; `memory-eval` has nothing to score until then.
- **A classifier that could replace the request kernel** — the zero-shot probe is
  measured and parked: `laya-multilingual` scored 0.31–0.39 on the intent set
  against the kernel's 0.89–1.00, and the vendor's own fine-tune reaches 0.766 on
  its benchmark. Next: a few hundred labelled queries, a trained head or a
  fine-tune, and the hybrid rule (confident → fast path, unsure → kernel) only if
  it clears the kernel on our own cases.
- Plugin architecture for custom modules
- Multi-GPU support
- **Multi-platform GPU support** — first iteration shipped in v13.0: the compose stack is split into a
  shared base plus per-vendor entry files, llama.cpp has a Vulkan image, `GpuInfo.accelerator` separates
  "a GPU" from "CUDA", AMD VRAM is read from sysfs and Intel from its Vulkan heaps, and the deploy scripts
  take `--platform`. Still planned:
  - **Vulkan path for image generation — implemented, NOT VERIFIED on hardware.** `sd_wrapper` now
    resolves the backend to `cuda` / `vulkan` / `cpu`, confirms a Vulkan device with `sd-cli
    --list-devices` instead of assuming one, and keeps the CUDA-only switches (`--rng cuda`,
    `--diffusion-fa`, the offload levels) off a Vulkan device; `modules/sd_cpp.py` gates on
    `GpuInfo.accelerator` instead of `cuda_detected`, and the AMD and Intel compose files build and run
    `flai-sd_cpp:vulkan`. What is left is one measurement on an actual AMD or Intel card: a generated
    image and its VRAM peak. Until then the AMD/Intel stacks are experimental by their own admission.
  - **GPU video and speech recognition on AMD/Intel** — LTX-Video needs a ROCm/SYCL PyTorch base and the
    Whisper `-gpu` image is CUDA-only.
  - **Real-hardware verification** of the AMD and Intel files; this project has NVIDIA and CPU only.
- **Redis 8.4.8** — published without the `-alpine` variant this stack uses. Bump when that tag appears;
  the advisory on that line (PUBLISH larger than 4 GB across the cluster bus) is unreachable for a
  standalone instance.
- **LTX-Video on a stable PyTorch** — the container force-installs a nightly (`2.12.0.dev+cu128`) for
  sm_120 support. Stable `cu128` wheels reach 2.11.0, so moving means a downgrade in exchange for
  reproducible builds; needs a rebuild and one measured clip on the reference card. **The CUDA floor does
  not move:** both builds are `cu128`, so an RTX 3060 on a CUDA 12.2+ driver keeps working exactly as it
  does today (minor-version compatibility plus `NVIDIA_DISABLE_REQUIRE`, the same mechanism the rest of the
  stack already relies on).
## Documented behaviour worth knowing

These are not roadmap items — they are current behaviour that surprises people often enough to be worth stating here:

- **One task at a time on the GPU.** FLAI serializes every GPU task behind a single lock and cleans VRAM between them. Multi-GPU work would need a different serialization model, which is why it sits in Planned rather than In Progress.
- **Context windows are auto-fitted at deployment** from the GGUF metadata on disk plus measured RAM/VRAM. They are not hand-configured. See [CONTEXT.md](CONTEXT.md) and [VRAM_MANAGEMENT.md](VRAM_MANAGEMENT.md).
- **The router decides, not keywords.** Request classification goes through the LLM router; there are no Python-level query filters. See [ARCHITECTURE.md](ARCHITECTURE.md).
- **Tavily is optional.** Without a per-user Tavily key, web search runs entirely on the local SearXNG metasearch engine. See [SEARCH.md](SEARCH.md).