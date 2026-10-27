# Video Generation

FLAI ships **two interchangeable video backends**, selected by `VIDEO_BACKEND`:

| | **LTX** (default) | **Wan** |
|---|---|---|
| Model | LTX-Video 2B 0.9.8 **distilled** | Wan 2.2 **TI2V 5B Turbo** (Q4_K_M) |
| Character | fast, long clips | slower, better motion and detail |
| Clip length | up to **10 s** (240 frames) | **5 s** (121 frames) |
| Resolution | 768×512 landscape, 512×768 portrait, 512×512 square | 832×480 landscape, 480×832 portrait, 640×640 square |
| Denoising steps | 8 | 4 (Turbo), CFG 1.0, Euler, flow-shift 3.0 |
| Measured VRAM peak | **~3.1 GB** | **15.0 GB** at 832×480×121 (9.0 GB with the encoder offloaded) |
| Measured time (16 GB card) | tens of seconds | **209 s** for a 5-second 480p clip |
| Container | `flai-ltxvideo` (PyTorch, port 7872) | `flai-sd` (stable-diffusion.cpp, port 7861) |
| Compose profile | `with-video` | `with-video-wan` |
| Deploy flag | `--with-video` | `--with-video-wan` |
| CPU-only hosts | supported (auto-downscale) | **refused** (see below) |

Both backends answer the same contract — `POST /v1/video/generations` — so the queue, the
VRAM gate, image-to-video and the chat UI are shared.

Choosing one is a deploy-time decision; the two profiles are mutually exclusive:

```bash
./deploy.sh --with-video      --download-models   # LTX  (default)
./deploy.sh --with-video-wan  --download-models   # Wan  (GPU only)
```

The script writes `VIDEO_BACKEND=ltx|wan` into `.env`. Switching later means re-running the
script with the other flag, or saving the same key in **Admin Panel → Settings**, which applies
without a restart (the module re-reads the backend, wrapper URL, model and timeout on every
request); the models of the inactive backend stay on disk and both containers can run at once.

## Generation parameters

Both backends ask the multimodal model for a prompt and a resolution through
`prompts/{lang}/create_video.template`; the template receives the backend's own resolution
set, frame rule and model name, so the request matches what the model was trained on.

| Parameter | LTX | Wan |
|---|---|---|
| Landscape | 768×512 | 832×480 |
| Portrait | 512×768 | 480×832 |
| Square | 512×512 | 640×640 |
| Frames | 240 (10 s) or 120 (5 s) | 121 (5 s), 81 (≈3.4 s), 49 (≈2 s) |
| Frame rate | 24 fps | 24 fps |
| Negative prompt | shared default | shared default |

Wan has two hard shape rules: frames must be **4n+1** (its VAE compresses time by four) and
both sides must be multiples of **32**. The wrapper snaps any request to those values, and the
application caps a longer request at 121 frames with a visible notice («Wan generates clips up
to 5 seconds — shortened the video from 10 s to 5 s»).

## LTX-Video 2B

- 8-step distilled pipeline, T5-XXL text encoder (~8.9 GB in bf16) kept on the CPU.
- Measured pipeline peak: **3057 MB** — the VRAM gate uses the measured value, not a constant.
- The source image for image-to-video is resized to **768 px** on the longest side; the output
  aspect follows the source: square → 512×512, wide → 768×512, tall → 512×768.
- 8/10 GB cards are capped at 512×512×120; a fragmented GPU (less than 6 GB free after the
  LLM unload) gets the same cap.
- **CPU hosts:** the planner (`plan_cpu_generation`) picks the largest format that fits both
  available RAM and the wall-clock budget (85 % of `LTX_VIDEO_TIMEOUT` unless
  `LTX_VIDEO_CPU_TIME_BUDGET_S` is set). Fallback chain: 768×512×240 → 384×256×120 @ 12 fps →
  256×192×57 @ 6 fps → 256×192×24 @ 6 fps. The static footprint is ~17.4 GB; a format that
  does not fit is refused up front with a localized error instead of timing out mid-generation.

## Wan 2.2 TI2V 5B

- Turbo GGUF (Apache-2.0), 4 steps, CFG 1.0, Euler sampler, flow-shift 3.0.
- It runs inside the **sd.cpp container** — the same one that generates images — through
  `POST /v1/video/generations`; no second Python/Torch stack is needed.
- sd.cpp writes avi/webm only, so the wrapper converts the clip to **mp4 (h264, yuv420p,
  faststart)** with the ffmpeg bundled in the image.
- **Text encoder:** umt5-xxl Q8_0 (~6 GB). It stays on the GPU when the card has 12 GB or more;
  below that the wrapper passes `--offload-to-cpu`, streams the weights from RAM and needs
  roughly 9 GB less VRAM (at the cost of speed). Threshold: `WAN_OFFLOAD_BELOW_VRAM_MB`.
- **VRAM presets** (calculated, calibrated against the measured peak):

  | Clip | Frames | Peak VRAM (GPU) | Peak VRAM (encoder offloaded) |
  |---|---|---|---|
  | 832×480 | 121 (5 s) | 15.0 GB | 9.0 GB |
  | 640×384 | 121 (5 s) | 13.0 GB | 7.0 GB |
  | 512×288 | 121 (5 s) | 11.7 GB | 5.7 GB |
  | 512×288 | 49 (2 s) | 10.6 GB | 4.6 GB |
  | 1280×704 | 121 (5 s) | 21.5 GB | 15.5 GB |

  The application walks that chain from the requested size downwards until the clip fits the
  free VRAM; on an out-of-memory error the wrapper repeats the same step-down. A card below
  12 GB therefore still gets a 5-second clip, and a 24 GB card can be asked for 720p.
- **Measured:** 832×480×121, 4 steps → **209 s** on a 16 GB card (207.8 s of model time, of
  which 128 s is the VAE decode), peak **15 340 MB** of card-wide usage.
- **CPU hosts are refused by default.** Measured on a 12-core CPU: 256×192×17 took **98 s**
  and ~12 GB of RAM; 512×288×49 already exceeded a 12 GB container cap and pushed a 30 GB
  host to 3.3 GB free. A 5-second 480p clip extrapolates to **~90 minutes and far more than
  12 GB of RAM**. `WAN_VIDEO_ALLOW_CPU=1` re-enables the attempt with the same RAM/time
  planner as LTX; without it the user gets a localized «Wan video generation requires a GPU».

## Shared behaviour

- **VRAM gate before generation.** Every video task unloads the llama.cpp models, waits until
  `llama-swap /running` reports nothing loaded and the estimated footprint is free
  (`VRAM_WAIT_TIMEOUT_S`, 120 s), then generates. A busy GPU is waited out, not reported as a
  failure.
- **Cleanup after generation.** LTX is unloaded through `POST /v1/unload` and, if its CUDA
  context survives, the container is restarted. Wan needs neither: `sd-cli` is a one-shot
  process, so the driver reclaims the VRAM when it exits.
- **Image-to-video.** The source image is resized to 768 px (LTX) or 832 px (Wan) and sent in
  the request body; both wrappers accept `image_data` and pass it to their engine (`-i` for
  sd.cpp).
- **Notices.** A downgraded resolution, a shortened clip or a refused generation is written as
  a system message in the chat, so the user learns why the result differs from the request.

## Configuration

```bash
# Backend selection (written by deploy.sh)
VIDEO_BACKEND=ltx                  # ltx | wan

# LTX-Video (flai-ltxvideo)
LTX_VIDEO_WRAPPER_URL=http://flai-ltxvideo:7872
LTX_VIDEO_MODEL=ltxv-2b-0.9.8-distilled
LTX_VIDEO_TIMEOUT=10800            # seconds; CPU hosts need the large value
LTX_VIDEO_CPU_TIME_BUDGET_S=0      # 0 = 85 % of LTX_VIDEO_TIMEOUT

# Wan 2.2 TI2V 5B (flai-sd)
WAN_VIDEO_URL=http://flai-sd:7861
WAN_VIDEO_MODEL=wan2.2-ti2v-5b-turbo-Q4_K_M.gguf
WAN_VIDEO_TIMEOUT=1800             # measured 209 s for 832×480×121
WAN_VIDEO_STEPS=4
WAN_VIDEO_CFG_SCALE=1.0
WAN_VIDEO_FLOW_SHIFT=3.0
WAN_VIDEO_ALLOW_CPU=0              # 1 = attempt Wan on a CPU-only host (very slow)
```

Wrapper-side limits live in the containers: `WAN_CLI_TIMEOUT` (1800 s, 7200 s on CPU),
`WAN_OFFLOAD_BELOW_VRAM_MB` (12000), `SD_CLI_TIMEOUT` (image generation).

## Required models

**LTX:**
1. `ltxv-2b-0.9.8-distilled.safetensors` (~5.9 GB) — diffusion transformer + VAE
2. `PixArt-alpha/PixArt-XL-2-1024-MS` text encoder / tokenizer — T5-XXL (~18 GB on disk,
   ~8.9 GB in bf16 on the CPU)

**Wan:**
1. `wan2.2-ti2v-5b-turbo-Q4_K_M.gguf` (~3.4 GB) — Turbo checkpoint
2. `Wan2.2_VAE.safetensors` (~1.4 GB)
3. `umt5-xxl-encoder-Q8_0.gguf` (~6 GB) — text encoder

```bash
# Download through the deploy script (picks the backend you chose)
./deploy.sh --download-models --with-video
./deploy.sh --download-models --with-video-wan

# LTX text encoder manually:
bash services/ltx_video/download-t5-encoder.sh
```

Wan files land in `services/sd_cpp/models/{diffusion_models,vae,text_encoders}` — the same
directories the image pipeline already uses, so nothing extra is mounted.

## Public API

`POST /v1/videos` accepts `prompt`, `width`, `height`, `num_frames`, `frame_rate` and `seed`.
Defaults and the duration ceiling follow the active backend: 768×512×240 (10 s) for LTX,
832×480×121 (5 s) for Wan. The response is a task id with a poll URL; the finished clip is
served by `GET /v1/flai/tasks/{id}/content`.

## Troubleshooting

- **«Wan video generation requires a GPU»** — the host is in CPU mode. Use
  `--with-video` (LTX) or set `WAN_VIDEO_ALLOW_CPU=1` and expect ~90 minutes per 5 s clip.
- **«Video generation requires GPU; available VRAM … is below safe threshold»** — another
  process holds the card, or the requested clip is larger than the tier. The chain steps down
  automatically; if nothing fits, simplify the request.
- **Clip is shorter than asked** — Wan is capped at 121 frames (5 s); the notice in the chat
  says so.
- **Clip looks lower-resolution than asked** — the VRAM chain picked the largest preset that
  fits. Check `docker logs flai-sd` (Wan) or `docker logs flai-ltxvideo` (LTX) for the chosen
  size.
