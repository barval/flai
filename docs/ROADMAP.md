# Roadmap

Planned and in-progress work for FLAI. Recent delivered work is recorded in [../CHANGELOG.md](../CHANGELOG.md); what each subsystem does *today* is documented in the file that owns it (see the Documentation Map in [../AGENTS.md](../AGENTS.md)).

This list is not a commitment. Items move when their priority changes, and an item may be dropped if it turns out not to be the right approach.

## 🔄 In Progress

- **Skills engine** — named capability bundles that extend the Tasks capabilities registry (`app/tasks/tools.py`): a skill appears as one more step tool without storage changes (the seam is already in place; see [TASKS.md](TASKS.md))
- **Advanced queue prioritization** — weighting and anti-starvation for the existing user classes (0/1/2) and task classes (3/4/5)

## 📅 Planned

- Plugin architecture for custom modules
- Multi-GPU support
- **Multi-platform GPU support** — extend FLAI to run on non-NVIDIA machines:
  - AMD / Intel via Vulkan for llama.cpp and stable-diffusion.cpp, ROCm for LTX-Video
  - Unified Docker Compose with per-platform profiles and env-driven deploy scripts

## Documented behaviour worth knowing

These are not roadmap items — they are current behaviour that surprises people often enough to be worth stating here:

- **One task at a time on the GPU.** FLAI serializes every GPU task behind a single lock and cleans VRAM between them. Multi-GPU work would need a different serialization model, which is why it sits in Planned rather than In Progress.
- **Context windows are auto-fitted at deployment** from the GGUF metadata on disk plus measured RAM/VRAM. They are not hand-configured. See [CONTEXT.md](CONTEXT.md) and [VRAM_MANAGEMENT.md](VRAM_MANAGEMENT.md).
- **The router decides, not keywords.** Request classification goes through the LLM router; there are no Python-level query filters. See [ARCHITECTURE.md](ARCHITECTURE.md).
- **Tavily is optional.** Without a per-user Tavily key, web search runs entirely on the local SearXNG metasearch engine. See [SEARCH.md](SEARCH.md).