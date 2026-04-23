# FLAI Services

This directory contains deployment configurations for AI backend services.

## llama.cpp (Required)

Replaces Ollama. Runs `llama-server` in router mode (`--model-dir`) to support dynamic model switching.

### Setup

1. **Download GGUF models** and place them in `services/llamacpp/models/`:

   ```bash
   mkdir -p services/llamacpp/models

   # Chat model (fast responses)
   wget -O services/llamacpp/models/qwen3-4b-instruct.Q4_K_M.gguf \
     "https://huggingface.co/Qwen/Qwen3-4B-Instruct-GGUF/resolve/main/qwen3-4b-instruct.Q4_K_M.gguf"

   # Reasoning model (complex tasks)
   wget -O services/llamacpp/models/gpt-oss-20b.Q4_K_M.gguf \
     "https://huggingface.co/openai/gpt-oss-20b-GGUF/resolve/main/gpt-oss-20b.Q4_K_M.gguf"

   # Multimodal model (image analysis)
   wget -O services/llamacpp/models/qwen3-vl-8b-instruct.Q4_K_M.gguf \
     "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/qwen3-vl-8b-instruct.Q4_K_M.gguf"

   # Embedding model (RAG)
   wget -O services/llamacpp/models/bge-m3.Q4_K_M.gguf \
     "https://huggingface.co/BAAI/bge-m3-gguf/resolve/main/bge-m3.Q4_K_M.gguf"
   ```

2. **Configure in `.env`:**
   ```bash
   LLAMACPP_URL=http://flai-llamacpp:8080
   ```

3. **Set models in Admin Panel** (`/admin` → Models tab):
   - Select the GGUF filename for each module (Chat, Reasoning, Multimodal, Embedding)
   - The server will dynamically load/unload models as needed

### Distributed Deployment

To run llama-server on a remote machine:

```bash
# On the remote GPU server
docker run -d \
  --name flai-llamacpp \
  --gpus all \
  -p 8033:8033 \
  -v /path/to/models:/models \
  ghcr.io/ggml-org/llama.cpp:server-cuda \
  --models-dir /models/ --host 0.0.0.0 --port 8033 --n-gpu-layers -1
```

Then set `LLAMACPP_URL=http://remote-ip:8033` in FLAI's `.env`.

## stable-diffusion.cpp (Optional)

Replaces Automatic1111. Provides text-to-image generation.

### Supported model types

#### Z_image_turbo (fast generation)
```bash
mkdir -p services/sd_cpp/models/{diffusion_models,vae,text_encoders}

# Diffusion model
wget -O services/sd_cpp/models/diffusion_models/z_image_turbo-Q8_0.gguf \
  "https://huggingface.co/bartowski/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q8_0.gguf"

# VAE
wget -O services/sd_cpp/models/vae/ae.safetensors \
  "https://huggingface.co/bartowski/Z-Image-Turbo-GGUF/resolve/main/ae.safetensors"

# Text encoder (LLM) — shared with editing
wget -O services/sd_cpp/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  "https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507-GGUF/resolve/main/qwen3-4b-instruct-2507-q4_k_m.gguf"
```
**Params:** cfg_scale=1.0, steps=10, flow_shift=2, 1024x1024, no negative_prompt.

### Image Editing (Flux.2 Klein 4B)

Requires separate model files for editing. Editing runs independently from generation.

```bash
mkdir -p services/sd_cpp/models/{diffusion_models,vae,text_encoders}

# Diffusion model
wget -O services/sd_cpp/models/diffusion_models/flux-2-klein-4b-Q8_0.gguf \
  "https://huggingface.co/bartowski/FLUX.2-Klein-dev-GGUF/resolve/main/flux-2-klein-4b-Q8_0.gguf"

# VAE
wget -O services/sd_cpp/models/vae/flux2_ae.safetensors \
  "https://huggingface.co/bartowski/FLUX.2-dev-GGUF/resolve/main/flux2_ae.safetensors"

# Text encoder (LLM) — shared with Z-Image Turbo
# Qwen3-4B-Instruct-2507-Q4_K_M.gguf (already downloaded for generation)
```
**Params:** cfg_scale=1.0, steps=4, sampling_method=euler, 1024x1024, uses reference image mode (`-r`).

### Classic SD (SDXL, SD 1.5)
Traditional diffusion models with CLIP/T5XXL text encoders.
**Params:** cfg_scale=7.0, steps=30, negative_prompt supported.

### Configuration in `.env`

```bash
SD_CPP_URL=http://flai-sd:7860

# Z_image_turbo defaults:
SD_CPP_DEFAULT_CFG_SCALE=1.0
SD_CPP_DEFAULT_STEPS=10
SD_CPP_DEFAULT_WIDTH=1024
SD_CPP_DEFAULT_HEIGHT=1024
SD_CPP_TIMEOUT=300
```

# Classic SD defaults (uncomment if using SDXL):
# SD_CPP_DEFAULT_CFG_SCALE=7.0
# SD_CPP_DEFAULT_STEPS=30
# SD_CPP_DEFAULT_WIDTH=512
# SD_CPP_DEFAULT_HEIGHT=512
```

## Whisper ASR (Optional, unchanged)

Uses `faster_whisper` via Docker. No changes from previous setup.

## Piper TTS (Optional, unchanged)

Uses ONNX Piper models. No changes from previous setup.

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
   spa=192.168.1.101:554:Спальня
   gos=192.168.1.102:554:Гостиная
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
