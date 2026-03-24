<div align="center">
  <img src="docs/logo.png" alt="Fully Local AI (FLAI)" width="200">

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

### 🤖 Core AI Capabilities
- 💬 **Intelligent Chat** – smart request routing (fast models for simple queries, powerful models for complex reasoning)
- 🧠 **Advanced Reasoning** – dedicated model for calculations, code generation, creative writing
- 🔍 **Multimodal Analysis** – upload images and ask questions about their content
- 🎨 **Image Generation** – create images from text using Stable Diffusion with automatic prompt optimization
- 🎤 **Voice Transcription** – convert voice messages to text using Whisper ASR
- 🗣️ **Text-to-Speech** – hear responses spoken aloud via Piper TTS (male/female voice)

### 📁 Document & Knowledge Management
- 📚 **RAG with Qdrant** – upload documents (PDF, DOC, DOCX, TXT) and ask questions about their content
- 🗂️ **Chat Sessions** – multiple independent conversations with auto-titling
- 💾 **Export Chats** – save conversations as HTML files with embedded media

### 🏠 Home Integration (Optional)
- 📹 **Camera Surveillance** – request snapshots from IP cameras and analyze them with multimodal models
- 🔐 **Access Control** – granular camera permissions per user via admin panel

### 🔒 Privacy & Security
- 🏠 **100% Local** – all processing happens on your hardware; no data leaves your network
- 🔐 **Session-based Auth** – secure user authentication with password hashing
- 🛡️ **File Access Control** – uploaded files are served only to authorized users
- 🧹 **Data Isolation** – each user's sessions, messages, and documents are strictly separated

### 👥 User Experience
- 🌐 **Multi-language Support** – full interface and AI responses in Russian and English
- 🌓 **Dark/Light Theme** – toggle between themes with persistent preference storage
- 🎚️ **Voice Gender Selection** – choose male or female voice for TTS responses
- 📊 **Request Queue** – real-time status tracking with position indicators for queued requests
- 📎 **File Attachments** – support for images, audio files, and documents in conversations
- 🔔 **Notifications** – unread message indicators and blinking status icons for processing/queued requests

### ⚙️ Administration
- 👤 **User Management** – add, edit, delete users; change passwords; assign service classes
- 🔑 **Camera Permissions** – control which users can access which cameras (Optional)
- 🤖 **Model Management** – select and configure models for chat, reasoning, multimodal, and embedding directly from the admin panel  
- 📈 **System Monitoring** – view database sizes and system statistics
- 🔧 **CLI Tools** – manage admin password via Flask CLI command

---

## 🏗️ Architecture

FLAI is a modular Flask application that orchestrates several self-hosted AI services.

### Core Components

| Component | Purpose | Technology | Default Port |
|-----------|---------|------------|--------------|
| **Flask Web** | Web interface, routing, API | Python | 5000 |
| **Ollama** | LLM inference (chat, reasoning, multimodal) | Go + llama.cpp | 11434 |
| **Automatic1111** | Stable Diffusion image generation | Python + PyTorch | 7860 |
| **Whisper ASR** | Speech-to-text transcription | OpenAI Whisper | 9000 |
| **Piper TTS** | Text-to-speech synthesis | ONNX + Piper | 18888 |
| **Qdrant** | Vector database for RAG | Rust | 6333 |
| **Redis** | Request queue management | C | 6379 |
| **SQLite** | User accounts, sessions, messages | Embedded SQL | -- |

### Distributed Deployment

Each service can run on separate machines for load distribution:
```text
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Web App   │────▶│   Ollama    │────▶│     GPU     │
│  (Flask)    │     │  (Node 1)   │     │   Server    │
└─────────────┘     └─────────────┘     └─────────────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐
│   Ollama    │────▶│     GPU     │
│  (Node 2)   │     │   Server    │
└─────────────┘     └─────────────┘
```
Set up separate Ollama URLs for each type of model in the Admin Panel (`/admin`).

---

## 📋 System Requirements

### Hardware Recommendations
| Component | Minimum | Recommended | With GPU |
|-----------|---------|-------------|----------|
| **RAM** | 8 GB | 16–32 GB | 32+ GB |
| **CPU** | 4 cores | 4+ cores | 8+ cores |
| **GPU** | Optional | NVIDIA 8+ GB VRAM | NVIDIA 16+ GB VRAM |
| **Storage** | 20 GB | 60+ GB SSD | 100+ GB SSD NVMe |

### Software Prerequisites
- Linux server (or Windows/macOS with Docker Desktop)
- Docker Engine ≥ 20.10
- Docker Compose ≥ 2.0
- Internet connection (only for initial model downloads)
> 💡 **Note**: After downloading models, FLAI works completely offline.

---

## 🔧 Configuration

### All-in-One Docker Compose
For running all services on a single machine, use `docker-compose.all.yml`:
```yaml
# docker-compose.all.yml
version: '3.8'

services:
  # ============================================================
  # WEB APPLICATION (Required)
  # ============================================================
  web:
    build: .
    container_name: flai-web
    ports:
      - "5000:5000"
    depends_on:
      - redis
    volumes:
      - .//app/data
      - ./.env:/app/.env:ro
    env_file:
      - .env
    environment:
      - REDIS_URL=redis://redis:6379/0
      # Ollama URLs per model type (for distributed deployment)
      - OLLAMA_CHAT_URL=http://ollama:11434
      - OLLAMA_REASONING_URL=http://ollama:11434
      - OLLAMA_MULTIMODAL_URL=http://ollama:11434
      - OLLAMA_EMBEDDING_URL=http://ollama:11434
    # GPU Support: Uncomment for NVIDIA GPU
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
    networks:
      - flai_network
    restart: unless-stopped

  # ============================================================
  # REDIS (Required - Request Queue)
  # ============================================================
  redis:
    image: redis:8.0.6-alpine
    container_name: flai-redis
    ports:
      - "6379:6379"
    volumes:
      - redis-/data
    command: redis-server --appendonly yes
    networks:
      - flai_network
    restart: unless-stopped
    # Disable if using external Redis:
    # Comment out this entire service block

  # ============================================================
  # OLLAMA (Required - LLM Inference)
  # ============================================================
  ollama:
    image: ollama/ollama:latest
    container_name: flai-ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama:/root/.ollama
    environment:
      - OLLAMA_REQUEST_TIMEOUT=1200s
      - OLLAMA_MAX_LOADED_MODELS=1
      - OLLAMA_KEEP_ALIVE=0
    # GPU Support: Uncomment for NVIDIA GPU
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
    networks:
      - flai_network
    restart: unless-stopped
    # Disable if using external Ollama:
    # Comment out this entire service block

  # ============================================================
  # AUTOMATIC1111 (Optional - Image Generation)
  # ============================================================
  automatic1111:
    image: siutin/stable-diffusion-webui-docker:latest-cuda
    container_name: flai-sd
    ports:
      - "7860:7860"
    volumes:
      - ./services/automatic1111/models:/app/stable-diffusion-webui/models
      - ./services/automatic1111/outputs:/app/stable-diffusion-webui/outputs
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
      - NVIDIA_REQUIRE_CUDA=cuda>=12.1
    # GPU Support: Required for reasonable performance
    # runtime: nvidia
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
    networks:
      - flai_network
    restart: unless-stopped
    profiles:
      - with-image-gen
    # Disable if not using image generation:
    # Comment out this entire service block OR use profiles

  # ============================================================
  # WHISPER ASR (Optional - Speech Recognition)
  # ============================================================
  whisper:
    image: onerahmet/openai-whisper-asr-webservice:latest
    container_name: flai-whisper
    ports:
      - "9000:9000"
    environment:
      - ASR_MODEL=medium
      - ASR_ENGINE=faster_whisper
      - ASR_DEVICE=cpu
    # GPU Support: Uncomment for faster transcription
    # environment:
    #   - ASR_DEVICE=cuda
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
    volumes:
      - ~/.cache/huggingface:/root/.cache/huggingface
    networks:
      - flai_network
    restart: unless-stopped
    profiles:
      - with-voice
    # Disable if not using voice features:
    # Comment out this entire service block

  # ============================================================
  # PIPER TTS (Optional - Text-to-Speech)
  # ============================================================
  piper:
    build:
      context: ./services/piper
      dockerfile: Dockerfile.piper
    container_name: flai-piper
    ports:
      - "18888:8888"
    volumes:
      - ./services/piper/piper_models:/app/models
    environment:
      - PIPER_MODEL_DIR=/app/models
    # GPU Support: Not needed (CPU-only)
    networks:
      - flai_network
    restart: unless-stopped
    profiles:
      - with-voice
    # Disable if not using voice features:
    # Comment out this entire service block

  # ============================================================
  # QDRANT (Optional - RAG Vector Database)
  # ============================================================
  qdrant:
    image: qdrant/qdrant:latest
    container_name: flai-qdrant
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant-/qdrant/storage
    environment:
      - QDRANT__SERVICE__API_KEY=${QDRANT_API_KEY:-}
      - QDRANT__SERVICE__ENABLE_TLS=0
    # GPU Support: Not typically needed
    networks:
      - flai_network
    restart: unless-stopped
    profiles:
      - with-rag
    # Disable if not using document search:
    # Comment out this entire service block

networks:
  flai_network:
    driver: bridge

volumes:
  redis-
  ollama:
  qdrant-
```

### Usage Examples
```bash
# Start all services
docker-compose -f docker-compose.all.yml up -d

# Start without image generation (saves GPU memory)
docker-compose -f docker-compose.all.yml --profile with-voice --profile with-rag up -d

# Start with everything
docker-compose -f docker-compose.all.yml --profile with-image-gen --profile with-voice --profile with-rag up -d

# Stop all services
docker-compose -f docker-compose.all.yml down

# View logs
docker-compose -f docker-compose.all.yml logs -f web
```

### Distributed Deployment (Multiple Machines)
For load distribution across multiple Ollama nodes:

1. Machine 1 (Web + Chat Models):
```bash
# .env on Machine 1
OLLAMA_CHAT_URL=http://machine1:11434
OLLAMA_REASONING_URL=http://machine2:11434
OLLAMA_MULTIMODAL_URL=http://machine3:11434
OLLAMA_EMBEDDING_URL=http://machine1:11434
```
2. Machine 2 (Reasoning Models)
```bash
# Run only Ollama
docker-compose -f services/ollama/docker-compose.yml up -d
```
3. Machine 3 (Multimodal Models):
```bash
# Run only Ollama
docker-compose -f services/ollama/docker-compose.yml up -d
```
Configure model URLs in Admin Panel → Models tab after first login.

---

## 🤖 Model Setup

### Required Models (Pull After Starting Ollama)
```bash
# Chat/Router model (fast responses)
docker exec flai-ollama ollama pull qwen3:4b-instruct-2507-q4_K_M

# Multimodal model (image analysis)
docker exec flai-ollama ollama pull qwen3-vl:8b-instruct-q4_K_M

# Reasoning model (complex tasks)
docker exec flai-ollama ollama pull gpt-oss:20b

# Embedding model (RAG document search)
docker exec flai-ollama ollama pull bge-m3:latest
```

### Configure Models in Admin Panel
1. Log in as admin and go to `/admin` → Models tab
2. For each module (Chat, Reasoning, Multimodal, Embedding):
   **Step 1: Specify Ollama URL**
   - Check the "Local" checkbox if Ollama runs on the same machine (URL auto-fills to `http://ollama:11434`)
   - Uncheck "Local" and enter custom URL for distributed deployment (e.g., `http://192.168.1.50:11434`)
   - Status icon shows connection status (✅ available / ❌ unavailable)
   **Step 2: Refresh Model List**
   - Click the 🔄 Refresh button to fetch available models from Ollama
   - Wait for the dropdown to populate with model names
   **Step 3: Select Model & Configure**
   - Select desired model from the dropdown
   - Model details appear below (architecture, parameters, context length)
   - Set parameters:
      * **Context Length**: Maximum tokens for context (must be ≤ model's max)
      * **Temperature**: Creativity (0.0–2.0, lower = more deterministic)
      * **Top P**: Nucleus sampling (0.0–1.0)
      * **Timeout**: Request timeout in seconds (0–1200)
   - Click Save to apply configuration
> 💡 Changing the embedding model triggers automatic re-indexing of all documents.

---













## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/barval/flai.git
cd flai
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your preferred settings (see Configuration section)
```

### 3. Start the Application
```bash
docker-compose up -d
```
The web interface will be available at `http://localhost:5000`.

### 4. Create Admin Account
```bash
docker exec -it flai-web-1 flask admin-password YourSecurePassword123
```
Log in with:
- Login: `admin`
- Password: `the password you just set`

---

## 🔧 Setting Up Dependent Services
FLAI integrates with several external AI services. Below are Docker Compose examples for running them alongside the main application.
Also see the examples in the `services` folder.
- ⚠️ Important: All services must share the same Docker network (flai_network) for proper communication.

### Create Shared Network
```bash
docker network create flai_network
```

### 🤖 Ollama (LLM Server)
```yaml
# services/ollama/docker-compose.yml
services:
  ollama:
    image: ollama/ollama:latest
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
    # Uncomment for GPU support:
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

Pull Required Models:
```bash
docker exec ollama ollama pull qwen3:4b-instruct-2507-q4_K_M      # Chat/Router
docker exec ollama ollama pull qwen3-vl:8b-instruct-q4_K_M        # Multimodal
docker exec ollama ollama pull gpt-oss:20b                        # Reasoning
docker exec ollama ollama pull bge-m3:latest                      # Embeddings (RAG)
```

### 🎨 Automatic1111 (Stable Diffusion)
```yaml
# services/automatic1111/docker-compose.yml
services:
  automatic1111:
    image: siutin/stable-diffusion-webui-docker:latest-cuda  # Use -cpu for CPU-only
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
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
      - NVIDIA_REQUIRE_CUDA=cuda>=12.1
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

networks:
  flai_network:
    external: true
```
Place your Stable Diffusion checkpoint (e.g., `cyberrealisticXL_v90.safetensors`) in the `./models` directory.
You can find the checkpoint here: `https://civitai.com/`

### 🎤 Whisper ASR (Speech-to-Text)
```yaml
# services/openai-whisper/docker-compose.yml
services:
  openai-whisper:
    image: onerahmet/openai-whisper-asr-webservice:latest        # CPU
    # image: onerahmet/openai-whisper-asr-webservice:latest-gpu  # GPU
    container_name: openai-whisper
    networks:
      - flai_network
    ports:
      - "9000:9000"
    environment:
      ASR_MODEL: "medium"                  # Options: tiny, base, small, medium, large
      ASR_ENGINE: "faster_whisper"         # Recommended for performance
      ASR_DEVICE: "cpu"                    # Change to "cuda" for GPU
    volumes:
      - ~/.cache/huggingface:/root/.cache/huggingface
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: always

networks:
  flai_network:
    external: true
```

### 🗣️ Piper TTS (Text-to-Speech)
```yaml
# services/piper/docker-compose.yml
services:
  piper:
    build:
      context: ./services/piper
      dockerfile: Dockerfile.piper
    container_name: piper
    networks:
      - flai_network
    ports:
      - "18888:8888"
    volumes:
      - ./piper_models:/app/models
    environment:
      - PIPER_MODEL_DIR=/app/models
    restart: unless-stopped

networks:
  flai_network:
    external: true
```

Download Voice Models:
```text
# Piper TTS voices to download
# Format: HuggingFace URL

# Russian male voice
https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx
https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json

# Russian female voice
https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx
https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json

# English male voice
https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json

# English female voice
https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx
https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json
```

### 🗄️ Qdrant (Vector Database for RAG)
```yaml
# services/qdrant/docker-compose.yml
services:
  qdrant:
    image: qdrant/qdrant:latest
    container_name: qdrant
    networks:
      - flai_network
    ports:
      - "6333:6333"   # HTTP API
      - "6334:6334"   # gRPC API (optional)
    volumes:
      - qdrant_data:/qdrant/storage
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}
      QDRANT__SERVICE__ENABLE_TLS: 0  # Disable TLS for local dev

volumes:
  qdrant_data:
    external: true
    name: qdrant_data

networks:
  flai_network:
    external: true
```

## ⚙️ Configuration (.env)
All settings are defined in the `.env` file. Key variables:

### Core Settings
| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret (generate a strong random value) | `x8#kL9$mP2@vN5!qR` |
| `TIMEZONE` | Local timezone for timestamps | `Europe/Moscow` |
| `REDIS_URL` | Redis connection string | `redis://redis:6379/0` |

### LLM Models (Ollama) - Initial defaults (overridden by admin panel after first run)
| Variable | Description | Example |
|----------|-------------|---------|
| `OLLAMA_URL` | Ollama API endpoint | `http://host.docker.internal:11434` |
| `LLM_CHAT_MODEL` | Fast model for chat/routing | `qwen3:4b-instruct-2507-q4_K_M` |
| `LLM_MULTIMODAL_MODEL` | Vision-capable model | `qwen3-vl:8b-instruct-q4_K_M` |
| `LLM_REASONING_MODEL` | Powerful model for complex tasks | `gpt-oss:20b` |
| `LLM_*_CONTEXT_WINDOW` | Context window size (tokens) | `8192`, `16384`, `32768` |
| `LLM_*_TEMPERATURE` | Creativity/randomness (0.0–1.0) | `0.1` (chat), `0.7` (reasoning) |
| `LLM_*_TOP_P` | Nucleus sampling parameter | `0.1`, `0.9` |
| `LLM_*_TIMEOUT` | Request timeout in seconds | `60`, `120`, `300` |

### Image Generation (Automatic1111)
| Variable | Description | Example |
|----------|-------------|---------|
| `AUTOMATIC1111_URL` | WebUI API endpoint | `http://host.docker.internal:7860` |
| `AUTOMATIC1111_MODEL` | Checkpoint filename | `cyberrealisticXL_v90.safetensors` |
| `AUTOMATIC1111_TIMEOUT` | Generation timeout (seconds) | `180` |
| `MAX_IMAGE_WIDTH` / `HEIGHT` | Max output resolution | `3840`, `2160` |
| `MAX_IMAGE_SIZE_MB` | Max upload size | `5` |

### Audio Processing
| Variable | Description | Example |
|----------|-------------|---------|
| `WHISPER_API_URL` | Whisper ASR endpoint | `http://host.docker.internal:9000/asr` |
| `WHISPER_API_TIMEOUT` | Transcription timeout | `120` |
| `PIPER_URL` | Piper TTS endpoint | `http://piper:8888/tts` |
| `PIPER_TIMEOUT` | TTS synthesis timeout | `30` |
| `MAX_VOICE_SIZE_MB` | Max voice recording size | `5` |
| `MAX_AUDIO_SIZE_MB` | Max uploaded audio size | `5` |

### Camera Integration (Optional)
| Variable | Description | Example |
|----------|-------------|---------|
| `CAMERA_API_URL` | Camera service endpoint | `http://host.docker.internal:5005` |
| `CAMERA_ENABLED` | Enable/disable camera module | `true` / `false` |
| `CAMERA_API_TIMEOUT` | Snapshot request timeout (seconds) | `15` |
| `CAMERA_CHECK_INTERVAL` | Health check interval (seconds) | `30` |
API for retrieving snapshots from surveillance cameras in various rooms: `https://github.com/barval/room-snapshot-api`

### RAG / Qdrant
| Variable | Description | Example |
|----------|-------------|---------|
| `QDRANT_URL` | Qdrant HTTP API endpoint | `http://host.docker.internal:6333` |
| `QDRANT_API_KEY` | API key for authentication | `your_secure_key` |
| `EMBEDDING_MODEL` | Ollama embedding model | `bge-m3:latest` |
| `RAG_CHUNK_SIZE` | Text chunk size for indexing | `500` |
| `RAG_CHUNK_OVERLAP` | Overlap between chunks | `50` |
| `RAG_TOP_K` | Number of chunks to retrieve | `10` |

### File & Document Settings
| Variable | Description | Example |
|----------|-------------|---------|
| `MAX_DOCUMENT_SIZE_MB` | Max uploaded document size | `25` |
| `UPLOAD_FOLDER` | Path for uploaded media | `data/uploads` |
| `DOCUMENTS_FOLDER` | Path for uploaded documents | `data/documents` |

### Advanced / Debug
| Variable | Description | Example |
|----------|-------------|---------|
| `TOKEN_CHARS` | Est. characters per token for context calc | `3` |
| `CONTEXT_HISTORY_PERCENT` | % of context window for history | `75` |
| `DEBUG_TRANSLATIONS` | Enable translation debugging | `false` |

---

## 👥 User Management

### Admin Panel (/admin)
- 👤 User Operations: Create, edit, delete user accounts
- 🔑 Password Management: Reset passwords for any user
- 🔐 Camera Permissions: Grant/revoke access to specific cameras per user
- 🤖 Model Management: Select and configure models for chat, reasoning, multimodal, and embedding.
Model details (architecture, context length, embedding length) are fetched from Ollama.
Changing the embedding model triggers automatic re‑indexing of all documents.
- 📊 System Stats: Monitor database sizes (users, chats, files, documents)
- 🎚️ Service Classes: Assign priority levels (0=highest, 2=lowest) for queue processing

### CLI Commands
```bash
# Set or change admin password
docker exec -it flai-web-1 flask admin-password NewPassword123
```

### User Self-Service
- 🌐 Language Switching: Toggle between Russian and English interface
- 🌓 Theme Toggle: Switch between light/dark modes (persisted per user)
- 🎚️ Voice Gender: Choose male/female voice for TTS responses
- 📁 Document Upload: Add PDF/DOC/TXT files for RAG-powered Q&A
- 💾 Chat Export: Save conversations as standalone HTML files

---

## 🗺️ Roadmap

### ✅ Completed
- Multi-model request routing (simple → fast model, complex → reasoning model)
- Multimodal image analysis with conversation history
- Image generation with automatic prompt optimization
- Voice transcription (Whisper) and synthesis (Piper TTS)
- Document upload + RAG with Qdrant semantic search
- Camera integration with permission system
- Redis-backed request queue with real-time status
- Full i18n support (RU/EN) with Flask-Babel
- Dark/light theme with persistent preferences
- HTML chat export with embedded media
- Admin panel with model management (select models for each role, view model info)
- Document index status display (pending, indexing, indexed, failed) with processing time
- Local processing indicator (for synchronous operations like transcription)

### 🔄 In Progress
- Long-term dialog memory (cross-session context persistence)
- Advanced RAG features: metadata filtering, hybrid search, re-ranking
- Mobile-responsive UI optimizations
- Performance Improvements
- Security Enhancements
- User activity analytics and usage statistics

### 📅 Planned
- Plugin architecture for custom modules
- API for external integrations (webhooks, REST endpoints)
- Backup/restore utilities for user data
- Multi-user collaboration features (shared sessions, document libraries)
- Local model fine-tuning utilities (LoRA, QLoRA support)

---

## 🤝 Contributing
Contributions are welcome! Here's how you can help:
- 🔍 Report Issues: Found a bug? Open an issue with reproduction steps
- 💡 Suggest Features: Have an idea? Start a discussion before coding
- 🛠️ Submit PRs: Fork, branch, code, test, and submit a pull request
- 📚 Improve Docs: Help refine documentation, examples, or translations

---

## 📄 License
This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.

<br> <div align="center"> Made with ❤️ for the local AI community </div>