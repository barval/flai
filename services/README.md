# FLAI Services

This directory contains deployment configurations for AI backend services.

## llama-swap (Required)

Runs [llama-swap](https://github.com/mostlygeek/llama-swap) (image `ghcr.io/mostlygeek/llama-swap:v255-cuda-b10991`, container `flai-llamaswap`, port 8080) — a llama.cpp proxy that loads/unloads GGUF models on demand. Models live in `services/llamacpp/models/` (one subdirectory per model, mounted as `/models`); llama-swap spawns a `llama-server` per model from the generated `config/llama-swap.yaml` (the web app rewrites it on every admin save).

### Setup

The automated deployment (`./deploy.sh` / `./deploy-ru.sh`) downloads the model set for your GPU tier automatically:

| Module | GPU default | CPU-only default |
|--------|-------------|------------------|
| Multimodal (chat/router/vision) | Qwen3VL-8B-Instruct-Q4_K_M (+ `mmproj-*.gguf`) | Qwen3VL-4B-Instruct-Q4_K_M |
| Reasoning | Qwen3.6-35B-A3B-UD-Q2_K_XL | gpt-oss-20b-mxfp4 |
| Embedding (RAG) | bge-m3-Q8_0 | bge-m3-Q8_0 |

Manual placement: each model goes into `services/llamacpp/models/<model-name>/<model-name>.gguf` (subdirectory per model). Since v10.0 there is **no separate chat module** — the single multimodal model serves chat, routing, and vision.

2. **Configure in `.env`:**
   ```bash
   LLAMACPP_BACKEND=llama-swap
   LLAMA_SWAP_URL=http://flai-llamaswap:8080
   ```

3. **Set models in Admin Panel** (`/admin` → Models tab):
   - Select the GGUF filename for each module (Multimodal, Reasoning, Embedding)
   - llama-swap loads/unloads models on demand; the multimodal model stays resident (`ttl=0`, preloaded on startup)

### Distributed Deployment

To run llama-swap on a remote GPU machine, deploy the `flai-llamaswap` service there (same image and `llama-swap.yaml`, models under `/models`), then point FLAI at it:

```bash
LLAMA_SWAP_URL=http://<remote-ip>:8080
```

(Only the llama-swap service needs to be remote — Whisper, SD, video, etc. still run on the FLAI host.)

## stable-diffusion.cpp (Optional)

Provides text-to-image generation and image editing.

### Supported model types

#### Z_image_turbo (fast generation)
```bash
mkdir -p services/sd_cpp/models/{diffusion_models,vae,text_encoders}

# Diffusion model
wget -O services/sd_cpp/models/diffusion_models/z_image_turbo-Q8_0.gguf \
  "https://huggingface.co/leejet/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q8_0.gguf"

# VAE
wget -O services/sd_cpp/models/vae/ae.safetensors \
  "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors"

# Text encoder (LLM) — separate copy for SD
wget -O services/sd_cpp/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
```
**Params:** cfg_scale=1.0, steps=10, flow_shift=2, 1024x1024, no negative_prompt.

### Image Editing (Flux.2 Klein 4B)

Requires separate model files for editing. Editing runs independently from generation.

```bash
mkdir -p services/sd_cpp/models/{diffusion_models,vae,text_encoders}

# Diffusion model
wget -O services/sd_cpp/models/diffusion_models/flux-2-klein-4b-Q8_0.gguf \
  "https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q8_0.gguf"

# VAE
wget -O services/sd_cpp/models/vae/flux2_ae.safetensors \
  "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors"

# Text encoder (LLM) — shared with Z-Image Turbo
# Qwen3-4B-Instruct-2507-Q4_K_M.gguf (already downloaded for generation)
```
**Params:** cfg_scale=1.0, steps=4, sampling_method=euler, 1024x1024, uses reference image mode (`-r`).

### Classic SD (SDXL, SD 1.5)
Traditional diffusion models with CLIP/T5XXL text encoders.
**Params:** cfg_scale=7.0, steps=30, negative_prompt supported.

### Configuration in `.env`

```bash
SD_WRAPPER_URL=http://flai-sd:7861

# Z_image_turbo defaults:
SD_CPP_DEFAULT_CFG_SCALE=1.0
SD_CPP_DEFAULT_STEPS=10
SD_CPP_DEFAULT_WIDTH=1024
SD_CPP_DEFAULT_HEIGHT=1024
SD_CPP_TIMEOUT=900

# Classic SD defaults (uncomment if using SDXL):
# SD_CPP_DEFAULT_CFG_SCALE=7.0
# SD_CPP_DEFAULT_STEPS=30
# SD_CPP_DEFAULT_WIDTH=512
# SD_CPP_DEFAULT_HEIGHT=512
```

## Whisper ASR (Optional)

Speech-to-text via `faster_whisper` (see [openai-whisper/README.md](openai-whisper/README.md)). Note: hosts with hardened AppArmor (Ubuntu 23.10+) need the `security_opt: [apparmor=unconfined]` option that both compose files already set (v11.4).

## Piper TTS (Optional)

ONNX Piper voices, port 8888 (see [piper/README.md](piper/README.md)). Since v11.2 Piper is the default TTS backend; **Kokoro** is the alternative (higher quality, selectable in Admin Panel → TTS): set `KOKORO_URL=http://flai-kokoro:8888/tts` and bring up `--profile with-voice-piper` / the kokoro service from `docker-compose.gpu.yml` (container `flai-kokoro`). Kokoro warms up one background Russian synthesis at startup (`KOKORO_WARMUP_G2P=1`) so the first ru phrase takes seconds, unloads the RUAccent G2P worker after idle (`KOKORO_G2P_IDLE_TIMEOUT`), and honors `KOKORO_TIMEOUT` (client synthesis timeout, seconds).

## Room Snapshot API (Optional)

Provides HTTP access to IP camera snapshots for the FLAI camera module.

### Setup

1. **Clone the service repository:**
   ```bash
   cd services/room-snapshot-api
   git clone https://github.com/barval/room-snapshot-api.git room-snapshot-api
   ```

2. **Configure cameras** in `room-snapshot-api/config/cameras.conf`:
   ```conf
   # Format: code=ip:port:name
   spa=192.168.1.101:554:Bedroom
   gos=192.168.1.102:554:Living room
   ```

3. **Set RTSP credentials** in `room-snapshot-api/.env`:
   ```bash
   cp room-snapshot-api/.env.example room-snapshot-api/.env
   # Edit .env and set RTSP_AUTH="username:password"
   ```

4. **Deploy:**
   ```bash
   ./deploy.sh local    # Same server as FLAI
   ./deploy.sh remote   # Separate server
   ```

See [room-snapshot-api/README.md](room-snapshot-api/README.md) for the full deployment guide.
