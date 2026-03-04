<div align="center">
 
  # Fully Local AI (FLAI)
  
  **FLAI — a fully local personal assistant powered by artificial intelligence.**  
  **Run your own AI stack entirely on-premises with no cloud dependencies.**  
  
  [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
  [![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
  [![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?logo=docker&logoColor=white)](https://www.docker.com/)

[English](README.md) | [Русский](README-ru.md)
</div>

---

## ✨ Features
- 💬 Intelligent Chat – interact with local LLMs via Ollama (routing, reasoning, multimodal).
- 🎨 Image Generation – create images from text using Stable Diffusion (Automatic1111).
- 🔍 Image Analysis – upload images and ask questions about them (multimodal model).
- 🎤 Voice Transcription – convert voice messages to text using Whisper ASR.
- 📹 Home Surveillance – request snapshots from IP cameras (optional) and optionally analyze them.
- 🗂️ Chat Sessions – multiple independent conversations with automatic titling and unread indicators.
- ⚙️ Admin Panel – manage users, set camera permissions, change passwords.
- 🚦 Request Queue – Redis‑backed queue with real‑time status and position tracking.
- 💾 Export Chats – save any conversation as a clean HTML file.
- 🔒 Fully Local – everything runs on your own hardware; no data ever leaves your network.

---

## 🧱 Architecture
FLAI is a Flask web application that orchestrates several self‑hosted AI services:
- **Ollama** – provides chat, reasoning, and multimodal models.
- **Automatic1111** – Stable Diffusion WebUI for image generation.
- **Whisper ASR** – speech‑to‑text service (faster‑whisper or OpenAI Whisper).
- **Redis** – manages the request queue to keep the web UI responsive.
- **SQLite** – stores user accounts, chat sessions, and messages.

All components can run in Docker containers, making deployment straightforward.

---

## 📋 Requirements
- Linux server (or Windows/macOS with Docker Desktop) with Docker and Docker Compose installed.
- At least 8 GB RAM (more recommended for larger models).
- NVIDIA GPU with CUDA support (for acceleration it is desirable, but not necessary).
- Internet connection only for downloading models; afterwards everything works offline.

---

## 🚀 Quick Start
### 1. Clone the repository
```bash
git clone https://github.com/barval/flai.git
cd flai
```
### 2. Prepare the configuration file
Copy the sample environment file:
```bash
cp .env.example .env
```
Edit the `.env` file by specifying your values (see `Configuration` below).
### 3. Launch the FLAI web app
```bash
docker-compose up -d
```
The application will be available at `http://localhost:5000`.
### 4. Create an admin user
```bash
docker exec -it flai_web_1 flask admin-password <your_admin_password>
```
Now you can log in with login `admin` and the password you set.

---

### 🔧 Setting Up Dependent Services
FLAI relies on external services: Ollama, Automatic1111, and Whisper.
You can run them on the same machine using the Docker Compose examples below.
Important: All services should be connected to the same Docker network (e.g., flai_network) so that FLAI can reach them via container names.

Create the shared network first:
```bash
docker network create flai_network
```

### 🤖 Ollama (LLM server)
Create a docker-compose.yml for Ollama:
```yaml
services:
  ollama:
    image: ollama/ollama
    container_name: ollama
    networks:
      - flai_network
    ports:
      - "11434:11434"
    volumes:
      - ollama:/root/.ollama
    environment:
      - OLLAMA_REQUEST_TIMEOUT=1200s
      - OLLAMA_MAX_LOADED_MODELS=1
      - OLLAMA_KEEP_ALIVE=0
    # Uncomment for GPU support
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]

volumes:
  ollama:
    external: true
    name: ollama

networks:
  flai_network:
    external: true
```
Pull the required models:
```bash
docker exec ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
docker exec ollama ollama pull qwen3-vl:8b-instruct-q4_K_M
docker exec ollama ollama pull gpt-oss:20b
```

### 🎨 Automatic1111 (Stable Diffusion WebUI)
```yaml
services:
  automatic1111:
    # image: siutin/stable-diffusion-webui-docker:latest-cuda # CPU
    image: siutin/stable-diffusion-webui-docker:latest-cuda   # GPU
    container_name: sd-webui
    networks:
      - flai_network
    ports:
      - "7860:7860"
    volumes:
      - ./models:/app/stable-diffusion-webui/models
      - ./embeddings:/app/stable-diffusion-webui/embeddings
      - ./outputs:/app/stable-diffusion-webui/outputs
    environment:
    # GPU
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
      - NVIDIA_REQUIRE_CUDA=cuda>=13.1
      - PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
    runtime: nvidia
    command:
      - /app/stable-diffusion-webui/webui.sh
      - --listen
      - --port=7860
      - --api
      - --api-log
      - --opt-sdp-attention
      - --medvram
      - --medvram-sdxl
      - --opt-split-attention

networks:
  flai_network:
    external: true
```
Place your Stable Diffusion checkpoint (e.g., `cyberrealisticXL_v90.safetensors`) into the `./models` directory.

### 🎤 Whisper ASR (speech‑to‑text)
```yaml
services:
  openai-whisper:
    image: onerahmet/openai-whisper-asr-webservice:latest         # CPU
    # image: onerahmet/openai-whisper-asr-webservice:latest-gpu   # GPU
    container_name: openai-whisper
    networks:
      - flai_network
    ports:
      - "9000:9000"
    environment:
      ASR_MODEL: "medium"                # or "small", "large"
      ASR_ENGINE: "faster_whisper"       # faster_whisper recommended
      ASR_DEVICE: "cpu"                  # change to "cuda" for GPU
    volumes:
      - ~/.cache/huggingface:/root/.cache/huggingface
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: always
    # GPU
#    deploy:
#      resources:
#        reservations:
#          devices:
#            - driver: nvidia
#              count: all
#              capabilities: [gpu]

networks:
  flai_network:
    external: true
```

---

## ⚙️ Configuration (.env)
All settings are defined in the `.env` file. Below are the most important variables; see `.env.example` for a complete list.

| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret (generate a strong one) | `mysecretkey` |
| `TIMEZONE` | Your local timezone | `Europe/Moscow` |
| `OLLAMA_URL` | Ollama API endpoint | `http://ollama:11434` |
| `LLM_CHAT_MODEL` | Router/chat model | `qwen3:4b-instruct-2507-q4_K_M` |
| `LLM_MULTIMODAL_MODEL` | Multimodal model for images | `qwen3-vl:8b-instruct-q4_K_M` |
| `LLM_REASONING_MODEL` | Model for complex reasoning tasks | `gpt-oss:20b` |
| `AUTOMATIC1111_URL` | Automatic1111 API endpoint | `http://sd-webui:7860` |
| `AUTOMATIC1111_MODEL` | Stable Diffusion checkpoint name | `cyberrealisticXL_v90.safetensors` |
| `WHISPER_API_URL` | Whisper ASR API URL | `http://openai-whisper:9000/asr` |
| `CAMERA_API_URL` | Camera snapshot API (if used) | `http://host.docker.internal:5005` |
| `FOOTER_TEXT` | Custom footer text | `FLAI v6.0 (с) 2026` |

---

## 👥 User Management
You can manage users through the Admin Panel (/admin) – add, edit, delete, change passwords and assign access rights to cameras.

The administrator account is created and changed by the command:
```bash
docker exec -it flai_web_1 flask admin-password <your_admin_password>
```

---

## 🗺️ Future Plans
- 🗣️ Text‑to‑Speech (TTS) – synthesize assistant replies using a local TTS engine (e.g., Coqui TTS, Piper) to enable voice interaction.
- 📚 RAG with Qdrant – implement Retrieval‑Augmented Generation over user‑uploaded documents (PDF, TXT, etc.) using a vector database (Qdrant) for semantic search.
- 🧠 Persistent Dialog Memory – maintain long‑term context across sessions by summarizing or storing conversation history.

---

## 📄 License
This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.

<br> <div align="center"> Made with ❤️ for the local AI community </div>