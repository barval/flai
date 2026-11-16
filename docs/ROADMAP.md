# Roadmap

Planned and in-progress work for FLAI. Recent delivered work is recorded in [../CHANGELOG.md](../CHANGELOG.md); what each subsystem does *today* is documented in the file that owns it (see the Documentation Map in [../AGENTS.md](../AGENTS.md)).

This list is not a commitment. Items move when their priority changes, and an item may be dropped if it turns out not to be the right approach.

## 🔄 In Progress

- *Nothing right now.* The queue prioritisation that sat here shipped in v13.4 — see `CHANGELOG.md`.

## 📅 Planned

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
  - **Vulkan path for image generation** — `sd_wrapper` forces CPU offload for any non-CUDA backend, so
    stable-diffusion.cpp needs a backend-aware device check and a Vulkan build that is actually exercised.
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
- **Alternative LLM inference engines (research)** — evaluate Strata, PolyStrata and other local
  engines as a possible complement to llama.cpp + llama-swap: OpenAI-compatible serving on Linux and
  in containers, the low-VRAM throughput they claim (8 GB cards), multimodal and embedding support,
  model formats, MIT licensing and maintenance risk. Deliverable: a short report with measurements
  on the reference hardware and a go/no-go for the serving layer.

## Documented behaviour worth knowing

These are not roadmap items — they are current behaviour that surprises people often enough to be worth stating here:

- **One task at a time on the GPU.** FLAI serializes every GPU task behind a single lock and cleans VRAM between them. Multi-GPU work would need a different serialization model, which is why it sits in Planned rather than In Progress.
- **Context windows are auto-fitted at deployment** from the GGUF metadata on disk plus measured RAM/VRAM. They are not hand-configured. See [CONTEXT.md](CONTEXT.md) and [VRAM_MANAGEMENT.md](VRAM_MANAGEMENT.md).
- **The router decides, not keywords.** Request classification goes through the LLM router; there are no Python-level query filters. See [ARCHITECTURE.md](ARCHITECTURE.md).
- **Tavily is optional.** Without a per-user Tavily key, web search runs entirely on the local SearXNG metasearch engine. See [SEARCH.md](SEARCH.md).