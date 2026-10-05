# Models: Download and Configuration

## Downloading GGUF models

The seed configuration ships without model files. Place them in the models directory before the first start.

### LLM Models (multimodal, reasoning, embedding)

```bash
mkdir -p services/llamacpp/models

# Multimodal model (chat/router/vision, always resident) — must be in subdirectory with mmproj
mkdir -p services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/Qwen3VL-8B-Instruct-Q4_K_M.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf"
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/mmproj-F16.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-8B-Instruct-F16.gguf"

# Reasoning model (complex tasks)
wget -O services/llamacpp/models/Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf \
  "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf"

# Embedding model (RAG)
wget -O services/llamacpp/models/bge-m3-Q8_0.gguf \
  "https://huggingface.co/gpustack/bge-m3-GGUF/resolve/main/bge-m3-Q8_0.gguf"
```

### Image Generation Models (Z_image_turbo)

```bash
mkdir -p services/sd_cpp/models/{diffusion_models,vae,text_encoders}

# Diffusion model
wget -O services/sd_cpp/models/diffusion_models/z_image_turbo-Q8_0.gguf \
  "https://huggingface.co/leejet/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q8_0.gguf"

# VAE
wget -O services/sd_cpp/models/vae/ae.safetensors \
  "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors"

# LLM text encoder (for SD, separate copy with Q4_K_M quantization)
wget -O services/sd_cpp/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
```

### Image Editing Models (Flux.2 Klein 4B)

```bash
# Diffusion model for editing
wget -O services/sd_cpp/models/diffusion_models/flux-2-klein-4b-Q8_0.gguf \
  "https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q8_0.gguf"

# VAE for editing
wget -O services/sd_cpp/models/vae/flux2_ae.safetensors \
  "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors"
```

> **Note**: Since v10.0 there is no standalone chat model. The only Qwen3-4B copy in the project is the SD text encoder (`Qwen3-4B-Instruct-2507-Q4_K_M.gguf` in `services/sd_cpp/models/text_encoders/`), required by stable-diffusion.cpp for image generation/editing.

> ⚠️ **Important**: Multimodal models **must** be placed in a subdirectory named after the model, with the `mmproj-*.gguf` file inside. The llama.cpp router automatically discovers and loads the projector.

### Video Generation Models (LTX-Video 2B)

```bash
# Create models directory
mkdir -p services/ltx_video/models

# Diffusion transformer + VAE checkpoint
wget -O services/ltx_video/models/ltxv-2b-0.9.8-distilled.safetensors \
  "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltxv-2b-0.9.8-distilled.safetensors"

# T5 text encoder (run the download script)
bash services/ltx_video/download-t5-encoder.sh
```

## Models, licenses and sizes


### LLM Models (llama.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf** | Reasoning (all tiers) | [Qwen License](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF) | ~12 GB |
| **Qwen3VL-8B-Instruct-Q4_K_M** | Multimodal — chat/router/vision | [Qwen License](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF) | ~5.5 GB + mmproj ~1.1 GB |
| **bge-m3-Q8_0** | Embedding (RAG) | [MIT License](https://huggingface.co/gpustack/bge-m3-GGUF) | ~0.6 GB |

### Image Generation Models (stable-diffusion.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Z-Image-Turbo (z_image_turbo-Q8_0)** | Image generation | [Apache 2.0](https://huggingface.co/leejet/Z-Image-Turbo-GGUF) | ~6.5 GB |
| **ae.safetensors** (VAE) | Variational autoencoder for Z-Image | [Apache 2.0](https://huggingface.co/Comfy-Org/z_image_turbo) | ~0.3 GB |
| **Qwen3-4B-Instruct-2507-Q4_K_M.gguf** | Text encoder for Z-Image | [Qwen License](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF) | ~2 GB |

### Image Editing Models (stable-diffusion.cpp)

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **Flux.2 Klein 4B (flux-2-klein-4b-Q8_0)** | Image editing (change colors, remove objects, stylize) | [Apache 2.0](https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF) | ~5 GB |
| **flux2_ae.safetensors** | VAE for Flux.2 editing | [Flux License](https://huggingface.co/Comfy-Org/flux2-dev) | ~0.3 GB |

### Video Generation Models

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **ltxv-2b-0.9.8-distilled.safetensors** | LTX-Video 2B diffusion transformer + VAE | [LTX-Video License](https://huggingface.co/Lightricks/LTX-Video) | ~5.9 GB |
| **PixArt T5-XXL (text_encoder)** | T5 text encoder for LTX-Video | [PixArt License](https://huggingface.co/PixArt-alpha/PixArt-XL-2-1024-MS) | ~18 GB (disk, float32) |

### Long-term Memory Models

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **nomic-embed-text-v1.5** | Text embedding for SLM retrieval | [Apache 2.0](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) | ~500 MB |

### Morphological Analysis

| Package | Purpose | License |
|---------|---------|---------|
| **pymorphy3** | Russian morphological analysis for camera room name recognition (generates declension forms) | [MIT License](https://github.com/kmike/pymorphy3) |

### Voice Models

| Model | Purpose | License | Approx. Size |
|-------|---------|---------|-------------|
| **en_US-ryan-medium** | English TTS (male) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 MB |
| **en_US-ljspeech-medium** | English TTS (female) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 MB |
| **ru_RU-dmitri-medium** | Russian TTS (male) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 MB |
| **ru_RU-irina-medium** | Russian TTS (female) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 MB |
| **Whisper medium** | Speech recognition | [MIT (OpenAI)](https://github.com/openai/whisper) | ~1.5 GB |

### Total Download Sizes (Approximate)

| Configuration | Approx. Download |
|---------------|-----------------|
| Minimal (Qwen3VL-4B + Qwen3.6-35B-A3B + bge-m3, 8 GB tier) | ~16 GB |
| CPU-only (Qwen3VL-4B + gpt-oss-20b-mxfp4 + bge-m3) | ~15 GB |
| Full LLM stack (Qwen3VL-8B + Qwen3.6-35B-A3B + bge-m3) | ~20 GB |
| + Image generation | ~29 GB |
| + Image editing | ~32 GB |
| + Voice (TTS + Whisper) | ~35 GB |
| + Video generation (LTX-Video + T5 encoder) | ~59 GB *(T5 encoder ~18 GB on disk in float32)* |
| + Long-term memory (SLM embedding model) | ~59.5 GB *(SLM adds ~500 MB)* |

> **Note**: After downloading models, FLAI works completely offline. No external scripts or modules are loaded at runtime.

---

## Model setup in the admin panel

### GGUF Model Structure

llama.cpp runs in **router mode** (`--models-dir`), dynamically loading models from a shared directory:

```
services/llamacpp/models/
├── Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf             # Reasoning (all tiers)
├── bge-m3-Q8_0.gguf                            # Embedding
├── Qwen3VL-8B-Instruct-Q4_K_M/                 # Multimodal (subdirectory!) — chat/router/vision
│   ├── Qwen3VL-8B-Instruct-Q4_K_M.gguf
│   └── mmproj-F16.gguf                         # Vision projector
```

> ⚠️ **Multimodal models require a subdirectory** with the projector file named `mmproj-*.gguf` inside. The model server auto-discovers and loads it.

### Configure Models in Admin Panel

1. Log in as admin and go to `/admin` → **Models** tab
2. For each module (Multimodal, Reasoning, Embedding):
   - Select the GGUF model from the dropdown, set parameters, click **Save**

> 💡 **Changing the embedding model triggers automatic re-indexing** of all documents.

### Downloading, Deleting and Offline Model Files (Model Hub)

- **Downloading:** the **Model Hub** tab (admin) searches Hugging Face, shows a GPU/RAM fit badge per file, and downloads with progress, resume and sha256 verification. Progress is in a persistent active-downloads area outside search results, survives changing searches and is restored after same-tab reloads. Displayed total size uses grouped whole MB rounded up. A successful download writes a `.hubmeta` marker that records the model's companion files (mmproj, MTP/draft head, text encoder).
- **Model and service files:** each multi-part GGUF model displays the total size of all its shards. Shard tails, imatrix, MTP/draft heads and FastMTP sidecars are service files rather than independent model choices. For supported noMTP models with Q4_0 and Q8_0 draft heads, select one variant; Q8_0 is selected by default.
- **Deleting:** downloaded models show a **✓ Downloaded** badge with a **Delete** button — in the Hub tab and in the **«Downloaded models»** panel on the Models tab. Deleting a Hub-downloaded model removes it **together with its companions**; a model currently selected as a module base is refused.
- **Offline:** when Hugging Face is unreachable the Hub tab shows a warning and points to the manual path: drop `.gguf` files into `/models`, then press **«Update model list»** in the Models tab.
- ⚠️ **Manually placed files:** a `.gguf` copied into `/models` by hand has **no** `.hubmeta` marker, so deleting it removes **only that file** — its companions (e.g. `mmproj-*.gguf`, MTP head, text encoder) are not tracked and must be removed manually.

### Model Parameters

| Parameter | Multimodal | Reasoning | Embedding |
|-----------|------------|-----------|-----------|
| Context Length | 32768 (auto-fit: 24576 on 16 GB, 16384 on 8 GB, 8192 CPU) | 32768 on 24+ GB, 24576 on 16 GB / 16384 on 8 GB / 8192 CPU (auto-fit) | 512 |
| Temperature | 0.7 | 0.7 | – |
| Top P | 0.9 | 0.9 | – |
| Repeat Penalty | 1.1 | 1.15 | – |
| Timeout (s) | 120 | 120 | 120 |

> **Note:** Router classification always uses `temperature=0.1` (hardcoded) for deterministic query routing, regardless of admin panel settings.

> **⚠️ Warning — Repeat Penalty:** do not set Repeat Penalty too high in the admin panel. The defaults (1.1 / 1.15) are deliberately conservative; values like 1.6 severely degrade reasoning models — verified by A/B testing on the same prompt: 1.6 produced a burned context (59K chars of runaway reasoning + truncated answer), an empty answer, and an answer in the wrong language (4/4 failed generations), while 1.15 produced 4/4 clean, complete answers. Symptoms of an excessive penalty: the model spends the whole context on `reasoning_content` and never answers, stops right after the intro sentence, or drifts off the requested language. Occasional repetition during long code generation is better handled by the built-in repetition-loop detector (server-side) than by raising this parameter.

### Model Selection Guide

| Component | Default | Recommended Alternative | Notes |
|-----------|---------|------------------------|-------|
| **Chat/router/vision** | Qwen3VL-8B Q4_K_M (~5.5 GB) | Qwen3VL-4B Q4_K_M (~2.5 GB, 8 GB GPU & CPU-only) | Single multimodal model serves all three roles; always resident. Requires subdirectory with `mmproj-*.gguf` |
| **Reasoning** | Qwen3.6-35B-A3B Q2_K_XL (~12 GB) | gpt-oss-20b-mxfp4 (~11.3 GB, default in CPU-only mode) | MoE architecture: ~3B active params, ~106 tok/s. GPU mode: Qwen3.6-35B on all tiers (8 GB uses partial CPU offload). CPU-only mode: gpt-oss-20b-mxfp4 (native MXFP4, CPU-friendly) |
| **Embedding** | bge-m3 Q8_0 (~0.6 GB) | — | Single model for all tiers |

> **Context windows:** defaults are auto-fitted at deployment (`app/database.py:_autofit_context`) — 32768 multimodal (24576 on 16 GB) and reasoning 32768/24576 on 24/16 GB tiers (both fit fully on the GPU at current quantization), 16384 on 8 GB, 8192 in CPU-only mode. The admin panel enforces the bounds (512 … GGUF architecture max) and — also for context-only changes — fit-checks every save against the RAM/VRAM budget, rejecting values that cannot fit, then plans a background dry-load of the new config and automatically rolls the change back (restoring `context_length`) if the backend fails to load it.

---
