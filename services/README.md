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
  -p 8080:8080 \
  -v /path/to/models:/app/models \
  ghcr.io/ggerganov/llama.cpp:server-cuda \
  --model-dir /app/models --host 0.0.0.0 --port 8080 --n-gpu-layers -1
```

Then set `LLAMACPP_URL=http://remote-ip:8080` in FLAI's `.env`.

## stable-diffusion.cpp (Optional)

Replaces Automatic1111. Provides basic text-to-image generation.

### Setup

1. **Download SD GGUF checkpoint** to `services/sd_cpp/models/`:

   ```bash
   mkdir -p services/sd_cpp/models

   # Example: RealVisXL v4 in GGUF format
   # Find available GGUF models on HuggingFace or CivitAI
   wget -O services/sd_cpp/models/realvisxl-v4.gguf \
     "https://huggingface.co/..."
   ```

2. **Configure in `.env`:**
   ```bash
   SD_CPP_URL=http://flai-sd:7860
   SD_CPP_MODEL=realvisxl-v4.gguf
   ```

## Whisper ASR (Optional, unchanged)

Uses `faster_whisper` via Docker. No changes from previous setup.

## Piper TTS (Optional, unchanged)

Uses ONNX Piper models. No changes from previous setup.
