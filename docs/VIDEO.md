# Video Generation (LTX-Video 2B)


The project uses **LTX-Video 2B 0.9.8 distilled** for video generation:

| Model | Steps | Frame Rate | Resolution | Notes |
|-------|-------|-----------|------------|-------|
| **LTX-Video 2B distilled** | 8 | 24 fps | up to 768×1344 | Distilled, single GPU (~6 GB VRAM) |

Video generation runs in a **separate GPU container** (via `--profile with-video`). Before generating, the llama.cpp LLM is automatically unloaded from VRAM to free memory. After generation, CUDA cache is cleared, LLM processes are re-unloaded, and the CUDA primary context is reset (`cuDevicePrimaryCtxReset`) to release all GPU memory back to the driver. The T5 text encoder (~8.9 GB in bf16) stays on CPU.

**Source image resize:** Images for video-from-image are resized to **768px** on the longest side before being sent to the LTX pipeline (reduces VRAM and network payload). A system notice shows the original vs resized dimensions.

**Aspect ratio matching:** When generating video from an image, the output video resolution is automatically adjusted to match the source image's aspect ratio: square images → 512×512, wide images (w/h > 1.2) → 768×512 landscape, tall images (w/h < 0.8) → 512×768 portrait.

**Required models:**
1. `ltxv-2b-0.9.8-distilled.safetensors` (~5.9 GB) — diffusion transformer + VAE
2. `PixArt-alpha/PixArt-XL-2-1024-MS` text encoder / tokenizer — T5-XXL encoder (~18 GB on disk in float32, ~8.9 GB in VRAM in bf16)

```bash
# Download via deploy script
./deploy.sh --download-models --with-video

# Or manually:
bash services/ltx_video/download-t5-encoder.sh
```

**Configuration:**
```bash
LTX_VIDEO_WRAPPER_URL=http://flai-ltxvideo:7872
LTX_VIDEO_MODEL=ltxv-2b-0.9.8-distilled
LTX_VIDEO_TIMEOUT=600
```

---
