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
- 🔐 **Session-based Auth** – secure user authentication with password hashing (Werkzeug)
- 🛡️ **File Access Control** – uploaded files are served only to authorized users
- 🧹 **Data Isolation** – each user's sessions, messages, and documents are strictly separated
- 🔑 **CSRF Protection** – Cross-Site Request Forgery protection for all forms
- 🚦 **Rate Limiting** – brute-force attack protection on login (5 attempts/minute)
- 🔒 **Session Security** – HttpOnly and SameSite cookies, secure flag for HTTPS
- 📝 **Audit Logging** – login attempts and admin actions are logged
- 🔐 **HMAC-signed Queue** – Redis queue tasks are signed to prevent tampering

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

Each service can run on separate machines for load distribution. See [services/README.md](services/README.md) for detailed deployment guides.

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

**Service Deployment Options:**
- **Local**: Run on same server as FLAI web app (internal Docker network)
- **Remote**: Run on separate server (requires firewall configuration)

Set up separate Ollama URLs for each type of model in the Admin Panel (`/admin`).

---

## 📋 System Requirements

### Hardware Recommendations
| Component | Minimum | Recommended | Optimal |
|-----------|---------|-------------|---------|
| **RAM** | 8 GB | 16–32 GB | 32+ GB |
| **CPU** | 4 cores | 4+ cores | 8+ cores |
| **GPU** | NVIDIA 8-12 GB VRAM | NVIDIA 16 GB VRAM | NVIDIA 16+ GB VRAM |
| **Storage** | 40 GB | 60+ GB SSD | 100+ GB SSD NVMe |

### Software Prerequisites
- Linux server (or Windows/macOS with Docker Desktop)
- Docker Engine ≥ 20.10
- Docker Compose ≥ 2.0
- Internet connection (only for initial model downloads)
> 💡 **Note**: After downloading models, FLAI works completely offline.

---

## 🚀 Quick Start
Get FLAI up and running in minutes with these simple steps:
> 💡 **Note**: You must have the **NVIDIA drivers** installed on the host machine and the **NVIDIA Container Toolkit**.

### 1. Clone and Configure
```bash
# Clone the repository
git clone https://github.com/barval/flai.git
cd flai

# Create directories and specify the owner
sudo mkdir -p data \
              data/uploads \
              data/documents
sudo chown -R 1000:1000 data

# Copy environment template
cp .env.example .env

# Generate a secure secret key
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")|" .env

# Generate an API key for Qdrant
sed -i "s|^QDRANT_API_KEY=.*|QDRANT_API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")|" .env

# Edit .env with your settings (timezone, API URLs, etc.)
nano .env
```

### 2. Prepare Additional Services (Optional but Recommended)
> 💡 Note: If you want to use image generation and voice features, complete the steps below. For chat only, skip to step 3.

#### 🎨 For Image Generation (Automatic1111):
```bash
# Create models directory
sudo mkdir -p services/automatic1111/models \
              services/automatic1111/models/Stable-diffusion \
              services/automatic1111/outputs
sudo chown -R 1000:1000 services

# Download a Stable Diffusion checkpoint (example: RealVisXL_V4.0)
# Replace with your preferred model from civitai.com or huggingface
wget -O services/automatic1111/models/Stable-diffusion/RealVisXL_V4.0.safetensors \
  "https://huggingface.co/SG161222/RealVisXL_V4.0/resolve/main/RealVisXL_V4.0.safetensors"

# In .env file, ensure these are set:
# AUTOMATIC1111_URL=http://flai-sd:7860
# AUTOMATIC1111_MODEL=RealVisXL_V4.0.safetensors
```

#### 🎤 For Voice Features (Piper TTS + Whisper):
```bash
# Create directory for voice models
mkdir -p services/piper/piper_models

# Download Russian voices (male and female)
curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx \
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx
curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx.json \
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json

curl -L -o services/piper/piper_models/ru_RU-irina-medium.onnx \
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx
curl -L -o services/piper/piper_models/ru_RU-irina-medium.onnx.json \
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json

# Download English voices (male and female)
curl -L -o services/piper/piper_models/en_US-ryan-medium.onnx \
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx  
curl -L -o services/piper/piper_models/en_US-ryan-medium.onnx.json \
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json  

curl -L -o services/piper/piper_models/en_US-ljspeech-medium.onnx \
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx  
curl -L -o services/piper/piper_models/en_US-ljspeech-medium.onnx.json \
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json

# In .env file, ensure these are set:
# PIPER_URL=http://flai-piper:8888/tts
# WHISPER_API_URL=http://flai-whisper:9000/asr
```

### 3. Start Services
Choose the option based on the features you need:
```bash
# Option A: Full functionality (Chat + Images + Voice + RAG)
docker-compose -f docker-compose.all.yml --profile with-image-gen --profile with-voice --profile with-rag up -d

# Option B: Chat and reasoning only (no images or voice)
docker-compose -f docker-compose.all.yml up -d

# Option C: Chat + Voice (no image generation)
docker-compose -f docker-compose.all.yml --profile with-voice up -d
```

### 4. Pull AI Models (Ollama)
```bash
# Wait for Ollama to start (about 30 seconds)
sleep 30

# Download models for chat, vision, reasoning, and search
docker exec flai-ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
docker exec flai-ollama ollama pull qwen3-vl:8b-instruct-q4_K_M
docker exec flai-ollama ollama pull gpt-oss:20b
docker exec flai-ollama ollama pull bge-m3:latest
```

### 5. Set Admin Password
```bash
# Create admin user with password
docker exec flai-web flask admin-password YourSecurePassword123
```

### 6. Access the Application
Open your browser and navigate to: <http://localhost:5000>

Login with:
- Login: `admin`
- Password: (the password you set in step 5)

### 7. Configure Models (First Login)
1. Go to **Admin Panel** → **Models** tab
2. For each module (Chat, Reasoning, Multimodal, Embedding):
    + Click 🔄 **Refresh** to load available models
    + Select the model you downloaded from the dropdown
    + Adjust parameters if needed (Context Length, Temperature, Top P, Timeout)
    + Click **Save**
3. For Image Generation: Ensure the checkpoint you downloaded is selected in the settings
4. For Voice: Ensure PIPER_URL is correctly set in `.env`

###  ✅ You're Ready!
Now you can:
- 💬 Have conversations with AI
- 🎨 Generate images (if Automatic1111 is configured)
- 🎤 Send voice messages and listen to responses (if Piper/Whisper is configured)
- 📚 Upload documents for search (if RAG profile is enabled)

---

## 🔧 Configuration

### Environment Variables (.env)

**Required:**
```bash
SECRET_KEY=your_secret_key_here      # Flask session secret
TIMEZONE=Europe/Moscow              # Your timezone
```

**Service URLs:**
```bash
OLLAMA_URL=http://flai-ollama:11434
AUTOMATIC1111_URL=http://flai-sd:7860
WHISPER_API_URL=http://flai-whisper:9000/asr
PIPER_URL=http://flai-piper:8888/tts
QDRANT_URL=http://flai-qdrant:6333
QDRANT_API_KEY=your_qdrant_api_key
CAMERA_API_URL=http://flai-room-snapshot-api:5005
```

**Service Retry Settings:**
```bash
SERVICE_RETRY_ATTEMPTS=15           # Connection retry attempts
SERVICE_RETRY_DELAY=2               # Delay between retries (seconds)
```

**Session Security:**
```bash
HTTPS_ENABLED=true                  # Set true for HTTPS proxy
PERMANENT_SESSION_LIFETIME=28800    # Session expiry (8 hours in seconds)
```

**Redis Queue:**
```bash
REDIS_RESULT_TTL=3600              # Result TTL (1 hour)
QUEUE_MAX_WAIT_TIME=300            # Max queue wait (5 minutes)
```

### Docker Configuration

**Gunicorn Settings (Dockerfile):**
```dockerfile
# Optimized for I/O bound operations
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "4", \
     "--worker-class", "gthread", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "wsgi:app"]
```

**Why 1 worker × 4 threads?**
- Minimal RAM usage (+40MB vs 1/1)
- Handles 4 concurrent connections
- Optimal for I/O bound (waiting for AI responses)
- Saves 280MB vs 4 workers

### All-in-One Docker Compose
For running all services on a single machine, use `docker-compose.all.yml`:

### Usage Examples
```bash
# Start all services
docker-compose -f docker-compose.all.yml up -d

# Start without image generation
docker-compose -f docker-compose.all.yml --profile with-voice --profile with-rag up -d

# Start with everything
docker-compose -f docker-compose.all.yml --profile with-image-gen --profile with-voice --profile with-rag up -d

# Stop all services
docker-compose -f docker-compose.all.yml down

# View logs
docker-compose -f docker-compose.all.yml logs -f web
```

### Distributed Deployment (Multiple Machines)

For load distribution across multiple servers, use standalone docker-compose files in `services/` directory:

1. **Web App + Redis** (Machine 1):
```bash
docker-compose -f docker-compose.all.yml up -d web redis
```

2. **Ollama - Chat Models** (Machine 2):
```bash
cd services/ollama
docker-compose -f docker-compose.gpu.yml up -d
```

3. **Ollama - Reasoning Models** (Machine 3):
```bash
cd services/ollama
docker-compose -f docker-compose.gpu.yml up -d
```

4. **Configure Model URLs** in Admin Panel → Models tab:
```
Chat: http://machine2:11434
Reasoning: http://machine3:11434
Multimodal: http://machine4:11434
Embedding: http://machine2:11434
```

**Firewall Configuration:**
```bash
# On each remote service machine
sudo ufw allow from <web-app-ip> to any port <service-port>
```

See [services/README.md](services/README.md) for complete deployment guides for each service.

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
1. Log in as admin and go to `/admin` → **Models** tab
2. For each module (Chat, Reasoning, Multimodal, Embedding):  
  #### **Step 1: Specify Ollama URL**  
   - Check the "Local" checkbox if Ollama runs on the same machine (URL auto-fills to `http://ollama:11434`)
   - Uncheck "Local" and enter custom URL for distributed deployment (e.g., `http://192.168.1.50:11434`)
   - Status icon shows connection status (✅ available / ❌ unavailable)  
  #### **Step 2: Refresh Model List**  
   - Click the 🔄 Refresh button to fetch available models from Ollama
   - Wait for the dropdown to populate with model names  
  #### **Step 3: Select Model & Configure**  
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

## 🎨 Image Generation Setup

### 1. Download Stable Diffusion Checkpoint
```bash
# Create models directory
mkdir -p services/automatic1111/models

# Download a Stable Diffusion checkpoint (example: RealVisXL_V4.0)
# Replace with your preferred model from civitai.com or huggingface
wget -O services/automatic1111/models/RealVisXL_V4.0.safetensors \
  "https://huggingface.co/SG161222/RealVisXL_V4.0/resolve/main/RealVisXL_V4.0.safetensors"
```

### 2. Configure in `.env`
```bash
AUTOMATIC1111_URL=http://flai-sd:7860
AUTOMATIC1111_MODEL=RealVisXL_V4.0.safetensors
AUTOMATIC1111_TIMEOUT=180
```

### 3. Enable in Docker Compose
Uncomment the `automatic1111` service or use profiles:
```bash
docker-compose -f docker-compose.all.yml --profile with-image-gen up -d
```

---

## 🎤 Voice Features Setup

### 1. Download Voice Models
```bash
mkdir -p services/piper/piper_models

# Russian male voice
curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx

curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json

# Russian female voice
curl -L -o services/piper/piper_models/ru_RU-irina-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx

curl -L -o services/piper/piper_models/ru_RU-irina-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json
```

### 2. Enable in Docker Compose
```bash
docker-compose -f docker-compose.all.yml --profile with-voice up -d
```

---

## 📚 RAG (Document Search) Setup

### 1. Configure Qdrant in `.env`
```bash
QDRANT_URL=http://flai-qdrant:6333
QDRANT_API_KEY=your_secure_api_key_here
EMBEDDING_MODEL=bge-m3:latest
RAG_CHUNK_SIZE=500
RAG_CHUNK_OVERLAP=50
RAG_TOP_K=5
```

### 2. Enable in Docker Compose
```bash
docker-compose -f docker-compose.all.yml --profile with-rag up -d
```

### 3. Upload Documents
  1. Log in to web interface
  2. Click Documents tab in sidebar
  3. Click ➕ to upload PDF, DOC, DOCX, or TXT files
  4. Wait for indexing to complete (status: ✅ Indexed)

---

## 📹 Camera Integration (Optional)
The camera module is not included in the main docker-compose and must be set up separately.

### 1. Deploy Camera API Service

The camera service is a separate project. Two deployment options available:

**Option A: Local Deployment (same server as FLAI)**
```bash
cd services/room-snapshot-api
./deploy.sh local
```

**Option B: Remote Deployment (separate server)**
```bash
# Clone the camera API repository
git clone https://github.com/barval/room-snapshot-api.git
cd room-snapshot-api

# Configure .env file
cp .env.example .env
# Edit .env with your camera URLs and credentials

# Deploy remotely
./deploy.sh remote

# Configure firewall
sudo ufw allow from <flai-server-ip> to any port 5005
```

See [services/room-snapshot-api/README.md](services/room-snapshot-api/README.md) for detailed instructions.

### 2. Configure FLAI to Use Camera Service
In FLAI's `.env` file:
```bash
# Enable camera module
CAMERA_ENABLED=true

# Camera API endpoint (adjust IP/port as needed)
# For local deployment:
CAMERA_API_URL=http://flai-room-snapshot-api:5005
# For remote deployment:
CAMERA_API_URL=http://<camera-server-ip>:5005

# Timeout for snapshot requests (seconds)
CAMERA_API_TIMEOUT=15

# Health check interval (seconds)
CAMERA_CHECK_INTERVAL=30
```

### 3. Configure Camera Permissions
  1. Log in to FLAI as admin
  2. Go to /admin → Users tab
  3. Edit a user and check the cameras they can access:
    Example:  
    - `tam` — tambour  
    - `hal` — hallway  
    - `cor` — corridor  
    - `bed` — bedroom  
    - `off` — office  
    - `chi` — children's room  
    - `liv` — living room  
    - `kit` — kitchen  
    - `bal` — balcony  

### 4. Using Cameras in Chat
Users with camera permissions can ask:
+ "Show the kitchen" → Returns snapshot from kitchen camera
+ "What's in the living room?" → Returns snapshot + AI analysis
+ "Is anyone in the office?" → Returns snapshot + AI analysis

---

## 👥 User Management

### Admin Panel Features
| Feature | Description |
|---------|-------------|
| 👤 User Operations | Create, edit, delete user accounts |
| 🔑 Password Management | Reset passwords for any user |
| 🔐 Camera Permissions | Grant/revoke camera access per user |
| 🤖 Model Management | Configure models per module type |
| 📊 System Stats | Monitor database and storage sizes |
| 🎚️ Service Classes | Set queue priority (0=highest, 2=lowest) |

### CLI Commands
```bash
# Set admin password
docker exec flai-web flask admin-password NewPassword123

# View help
docker exec flai-web flask --help
```

---

## 🔍 Monitoring & Health

### Health Check Endpoint

Comprehensive health check for all services:

```bash
curl http://localhost:5000/health
```

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2026-04-02T00:34:08.237346",
  "services": {
    "web": "ok",
    "database": "ok",
    "redis": "ok",
    "ollama": "ok"
  }
}
```

**Status values:**
- `ok` — all services healthy
- `degraded` — some services unavailable
- `error` — all services unavailable

### Prometheus Metrics

Prometheus-compatible metrics endpoint:

```bash
curl http://localhost:5000/metrics
```

**Available metrics:**
- `flai_web_info` — Service version
- `flai_queue_length` — Current queue length
- `flai_queue_processing` — Tasks being processed
- `flai_database_size_bytes` — Database file size
- `flai_requests_total` — Total requests counter
- `flai_uptime_seconds` — Service uptime

### API Documentation

Full API documentation available in OpenAPI format:
- **File:** `docs/openapi.yaml`
- **Format:** OpenAPI 3.0
- **Coverage:** All REST endpoints

View with Swagger UI or any OpenAPI-compatible viewer.

---

## 🧪 Testing

### Unit & Integration Tests

FLAI includes comprehensive test coverage for critical components:

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=app --cov=modules --cov-report=html

# Run specific test category
pytest tests/test_admin_routes.py
pytest tests/test_documents_routes.py
pytest tests/test_image_module.py
```

**Test Coverage:**
- `test_admin_routes.py` — Admin panel endpoints (17 tests)
- `test_documents_routes.py` — Document upload/RAG (16 tests)
- `test_image_module.py` — Image generation (16 tests)
- `test_queue.py` — Redis queue operations
- `test_audio_module.py` — Audio transcription
- `test_security.py` — Security features (CSRF, rate limiting, etc.)
- `test_integration.py` — End-to-end integration tests

### Load Testing

FLAI includes Locust-based load testing scripts.

### Setup
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install Locust
pip install locust
```

### Run Tests
```bash
# Web interface
locust -f tests/load/locustfile.py --host http://localhost:5000

# Headless mode (automated)
locust -f tests/load/locustfile.py --host http://localhost:5000 \
  --headless -u 10 -r 2 --run-time 1m
```

### Test User
Create test user before running:
- Login: `testuser`
- Password: `testpass`

> 💡  **Required:** block or delete the test user after the tests!

---

## 🗺️ Roadmap

### ✅ Completed
- Multi-model request routing (simple → fast, complex → reasoning)
- Multimodal image analysis with conversation history
- Image generation with automatic prompt optimization
- Voice transcription (Whisper) and synthesis (Piper TTS)
- Document upload + RAG with Qdrant semantic search
- Redis-backed request queue with real-time status
- Full i18n support (RU/EN) with Flask-Babel
- Dark/light theme with persistent preferences
- HTML chat export with embedded media
- Admin panel with model management
- Document index status display with processing time
- Camera integration with access rights system
- **SQLite WAL mode for better concurrency**
- **Load testing with Locust**
- **Separate Ollama URLs per model type (distributed deployment)**
- **Security enhancements:**
  * CSRF protection for all forms
  * Rate limiting on login (brute-force protection)
  * Session ownership validation
  * Path traversal protection
  * HMAC-signed Redis queue tasks
  * Security headers (CSP, X-Frame-Options, etc.)
  * Audit logging for security events
- **Standalone service deployment:**
  * Ollama (with GPU support)
  * Automatic1111 (Stable Diffusion)
  * Whisper ASR
  * Piper TTS
  * Qdrant (vector database)
  * Room Snapshot API (local/remote deployment)

### 🔄 In Progress
- Long-term dialog memory (cross-session context)
- Advanced RAG: metadata filtering, hybrid search, re-ranking
- Mobile-responsive UI optimizations
- Performance improvements
- Security enhancements
- User activity analytics

### 📅 Planned
- Plugin architecture for custom modules
- API for external integrations (webhooks, REST)
- Backup/restore utilities
- Multi-user collaboration features
- Local model fine-tuning (LoRA, QLoRA)

---

## 🤝 Contributing
Contributions are welcome!
- 🔍 Report Issues: Open an issue with reproduction steps
- 💡 Suggest Features: Start a discussion before coding
- 🛠️ Submit PRs: Fork, branch, code, test, submit
- 📚 Improve Docs: Help refine documentation and translations

---

## 📄 License
This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.

<br> <div align="center"> Made with ❤️ for the local AI community </div>
