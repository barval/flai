# Image Generation and Editing


## Generation Model

The project uses **Z_image_turbo** as the only image generation model:

| Model | Steps | CFG Scale | Resolution | Notes |
|-------|-------|-----------|------------|-------|
| **Z_image_turbo** | 10 | 1.0 | up to 1536×1536 | Fast, flow-matching |

All uploaded images are automatically resized to **1536px** on the longest side (configurable via `MAX_IMAGE_SIZE` in `.env`) to prevent Qwen3VL context overflow and reduce disk usage.

Configure via `SD_MODEL_TYPE` in `.env`:
```bash
SD_MODEL_TYPE=z_image_turbo
```

**«Draw something similar» with references** — attach example images to a generation request (in chat, or via `image_references` on `POST /v1/images/generations`). Each example is described by the multimodal model, the descriptions are prepended to the SD prompt as reference examples («Reference examples (draw something similar):»), and the real width×height of the reference is appended — the generation template gives the example's aspect ratio priority over the subject-based format rule. SD itself receives text only.

An image-attached chat message such as *«draw something similar»* is classified by the multimodal model into a dedicated `[-IMAGE-]` option (create a NEW similar image, distinct from `[-IMAGE-EDIT-]`), and the two image-chat handlers route it straight to generation with the attached examples.

**Quoted dialogue is not lettering.** Scene text often carries dialogue («Не добуду куропатку — беспременно быть войне», `"I will return by morning"`). Before the prompt builder, `strip_speech_for_image()` removes such quotes (and the `saying …` clause) unless the request explicitly asks for an inscription/sign/text; the final SD prompt then gets a positive `clean illustration without any text, letters, numbers, captions or watermarks` clause. The same rule is in both prompt templates. Without it the diffusion model tried to paint the quote and produced unreadable mixed-script lettering.

## Image Editing (Flux.2 Klein 4B)

Upload an image and ask to edit it (e.g., *"change the pupils to green"*, *"remove the second sun"*). The system uses:
1. **Multimodal model** (Qwen3VL) to analyze the image and generate an edit prompt
2. **Flux.2 Klein 4B** model via stable-diffusion.cpp to perform the edit
3. The original image is preserved except for the requested changes

Source images for editing are automatically resized to **1024px** on the longest side to avoid OOM on 16GB GPUs. A system notice shows the original vs resized dimensions if downscaled.

**Tone matching.** Flux.2 Klein tends to return a lighter, flatter image (measured on live edits: luminance 128 → 166, contrast 44 → 42). After every task repair/polish edit and every chat edit that does not explicitly ask to change lighting/colour, `_match_tone()` measures the source and result luminance (YCbCr Y mean/std) and, when the drift exceeds 6 levels or contrast drops below 90 % of the source, pulls the edited image fully back to the source exposure/contrast. Only the luminance channel is corrected, so colours and an intentionally changed style survive; a request like «make it brighter», «darker» or «black and white» skips the correction.

Editing uses separate model files and runs independently from generation — no conflict between the two.

## Descriptions for task illustrations (visual continuity)

A task that plans two or more `image_gen` steps first builds an internal **visual continuity sheet** (`tasks.visual_bible`): one style plus the fixed look of every character, place and prop named in the request. It is extracted by one reasoning call (`prompts/{ru,en}/task_continuity.template`) before the first generated image; `look` is **English only** (one concrete sentence with the identifying features), `aliases` are in the task language, `id` is lowercase Latin letters, digits and underscores. An edit-only task never extracts it.

For each scene `render_sheet` selects the cards named by the step goal and criterion (case-insensitive substring, then pymorphy3 lemmas; a multi-word alias matches word-wise) and prepends a block with `Style:` and grouped `Characters:` / `Places:` / `Props:` lines to the generation query. The multimodal prompt builder (`create_image_z_image_turbo.template`) turns that query into the final SD prompt (`[subject] + [action] + [setting] + [style] + [lighting] + [composition] + [quality]`); `ensure_entities_in_prompt` then appends the look of any selected card missing from that prompt, so a rephrasing cannot drop an entity, and `ensure_style_in_prompt` forces the sheet's style into the final prompt and removes the old fixed `photorealistic` tag unless the style itself is photoreal (the quality tags are now `high detail, sharp focus, masterpiece`). A replan with pending images marks the sheet stale and merges it (existing ids/looks kept verbatim). Extraction retries, repair edits and the best-effort candidate pick are described in `docs/TASKS.md`.

## stable-diffusion.cpp Build

The `sd_cpp` service is **built from source** during first `docker compose up`:
1. Clones `https://github.com/leejet/stable-diffusion.cpp`
2. Initializes git submodules (`ggml`, `thirdparty/*`)
3. Compiles with CUDA 13.0.1 (`cmake -DSD_CUDA=ON`) or without CUDA for CPU
4. Produces `sd-server` and `sd-cli` binaries

Each compose file builds **its own tagged image** via the `SD_BACKEND` build arg (see `Dockerfile.sd_cpp`):
- `docker-compose.gpu.yml` → `flai-sd_cpp:cuda` (`SD_BACKEND=cuda`)
- `docker-compose.cpu.yml` → `flai-sd_cpp:cpu` (`SD_BACKEND=cpu`; optional `vulkan` variant supported)

Because the tags differ, GPU and CPU images can live side by side — switching between the stacks (see «CPU-only mode» above) does not require rebuilding.

> ⏱️ **First build**: ~5-10 minutes depending on CPU. Subsequent builds use Docker cache.

## Configuration

```bash
# sd-wrapper HTTP API (port 7861)
SD_WRAPPER_URL=http://flai-sd:7861
SD_CPP_TIMEOUT=900                  # Timeout for gen/edit operations (seconds)
SD_CPP_DEFAULT_WIDTH=1024
SD_CPP_DEFAULT_HEIGHT=1024
SD_CPP_DEFAULT_CFG_SCALE=1.0
SD_CPP_DEFAULT_STEPS=10
```

Two timeouts are nested: the wrapper that runs `sd-cli` guards its own subprocess
(`SD_CLI_TIMEOUT`, default 600 s on GPU and 1800 s on CPU — an override applies to
both cases), while FLAI waits on the HTTP call for `SD_CPP_TIMEOUT` (900 s). The inner
guard is the one that reports a slow generation, so it must stay below the outer one.

---
