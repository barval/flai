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

An image-attached chat message such as *«нарисуй что-то похожее»* is classified by the multimodal model into a dedicated `[-IMAGE-]` option (create a NEW similar image, distinct from `[-IMAGE-EDIT-]`), and the two image-chat handlers route it straight to generation with the attached examples.

## Image Editing (Flux.2 Klein 4B)

Upload an image and ask to edit it (e.g., *"change the pupils to green"*, *"remove the second sun"*). The system uses:
1. **Multimodal model** (Qwen3VL) to analyze the image and generate an edit prompt
2. **Flux.2 Klein 4B** model via stable-diffusion.cpp to perform the edit
3. The original image is preserved except for the requested changes

Source images for editing are automatically resized to **1024px** on the longest side to avoid OOM on 16GB GPUs. A system notice shows the original vs resized dimensions if downscaled.

Editing uses separate model files and runs independently from generation — no conflict between the two.

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

---
