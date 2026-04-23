#!/usr/bin/env bash
# FLAI v8.0 — Single-Server Deployment Script

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Prerequisites ──
check_prereqs() {
    info "Checking prerequisites..."
    command -v docker &>/dev/null     || error "Docker is not installed. Install Docker first."
    command -v docker compose &>/dev/null || error "Docker Compose plugin is not installed."
    command -v git &>/dev/null        || error "Git is not installed."
    if ! command -v nvidia-smi &>/dev/null; then
        warn "No NVIDIA GPU detected — GPU acceleration will not be available."
    fi
    info "Prerequisites OK."
}

# ── Configuration ──
setup_env() {
    if [[ -f .env ]]; then
        warn ".env already exists — skipping configuration."
        return
    fi
    info "Creating .env from .env.example..."
    cp .env.example .env
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET/" .env
    info ".env created. Edit it to set models, URLs, and preferences."
}

# ── Generate docker-compose.override.yml for CPU mode ──
generate_cpu_override() {
    cat > docker-compose.override.yml <<'EOF'
# Auto-generated override for CPU-only deployment.
# Replaces GPU-accelerated services with CPU-optimised images.
services:
  llamacpp:
    image: ghcr.io/ggml-org/llama.cpp:server
    runtime: ""
    environment: {}
    deploy:
      resources:
        limits:
          cpus: '4.0'
          memory: 16G
        reservations:
          cpus: '2.0'
          memory: 8G
    command: >
      --models-dir /models/
      --models-preset /models/models-preset.ini
      --models-max 1
      --ctx-size 16384
      --host 0.0.0.0
      --port 8033
      --n-gpu-layers 0
      --embeddings
      --batch-size 2048
      --ubatch-size 2048

  sd_cpp:
    build:
      context: ./services/sd_cpp
      dockerfile: Dockerfile.sd_cpp-cpu
    runtime: ""
    environment: {}
    deploy:
      resources:
        limits:
          cpus: '8.0'
          memory: 24G
        reservations:
          cpus: '4.0'
          memory: 16G
EOF
    info "Created docker-compose.override.yml for CPU-only mode."
}

# ── Model download helpers ──
HF_DOWNLOAD() {
    local repo="$1" file="$2" dest="$3"
    if command -v huggingface-cli &>/dev/null; then
        huggingface-cli download "$repo" "$file" --local-dir "$dest" 2>&1
    else
        local url="https://huggingface.co/$repo/resolve/main/$file"
        info "Downloading $file from HuggingFace..."
        mkdir -p "$dest"
        curl -L -o "$dest/$file" "$url"
    fi
}

download_llamacpp_models() {
    info "Downloading llama.cpp models..."
    local MODEL_DIR="services/llamacpp/models"

    # Chat model
    if [[ ! -f "$MODEL_DIR/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" ]]; then
        info "Downloading Qwen3-4B-Instruct-2507-Q4_K_M.gguf (chat)..."
        HF_DOWNLOAD "bartowski/Qwen3-4B-Instruct-2507-GGUF" \
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf" "$MODEL_DIR"
    else
        warn "Qwen3-4B-Instruct-2507-Q4_K_M.gguf already exists — skipping."
    fi

    # Reasoning model (complex tasks)
    if [[ ! -f "$MODEL_DIR/gpt-oss-20b-mxfp4.gguf" ]]; then
        info "Downloading gpt-oss-20b-mxfp4.gguf (reasoning)..."
        HF_DOWNLOAD "openai/gpt-oss-20b-GGUF" \
            "gpt-oss-20b-mxfp4.gguf" "$MODEL_DIR"
    else
        warn "gpt-oss-20b-mxfp4.gguf already exists — skipping."
    fi

    # Multimodal model (with mmproj)
    if [[ ! -d "$MODEL_DIR/Qwen3VL-8B-Instruct-Q4_K_M" ]]; then
        info "Downloading Qwen3VL-8B-Instruct-Q4_K_M (multimodal)..."
        mkdir -p "$MODEL_DIR/Qwen3VL-8B-Instruct-Q4_K_M"
        HF_DOWNLOAD "bartowski/Qwen3VL-8B-Instruct-GGUF" \
            "Qwen3VL-8B-Instruct-Q4_K_M.gguf" "$MODEL_DIR/Qwen3VL-8B-Instruct-Q4_K_M"
        HF_DOWNLOAD "bartowski/Qwen3VL-8B-Instruct-GGUF" \
            "mmproj-F16.gguf" "$MODEL_DIR/Qwen3VL-8B-Instruct-Q4_K_M"
    else
        warn "Qwen3VL-8B-Instruct-Q4_K_M already exists — skipping."
    fi

    # Embedding model (for RAG)
    if [[ ! -f "$MODEL_DIR/bge-m3-Q8_0.gguf" ]]; then
        info "Downloading bge-m3-Q8_0.gguf (embeddings)..."
        HF_DOWNLOAD "bartowski/bge-m3-GGUF" \
            "bge-m3-Q8_0.gguf" "$MODEL_DIR"
    else
        warn "bge-m3-Q8_0.gguf already exists — skipping."
    fi
}

download_sd_cpp_models() {
    info "Downloading stable-diffusion.cpp models..."
    local DIFF_DIR="services/sd_cpp/models/diffusion_models"
    local VAE_DIR="services/sd_cpp/models/vae"
    local TXT_DIR="services/sd_cpp/models/text_encoders"

    # Z-Image Turbo (image generation)
    if [[ ! -f "$DIFF_DIR/z_image_turbo-Q8_0.gguf" ]]; then
        info "Downloading z_image_turbo-Q8_0.gguf..."
        HF_DOWNLOAD "bartowski/Z-Image-Turbo-GGUF" \
            "z_image_turbo-Q8_0.gguf" "$DIFF_DIR"
    else
        warn "z_image_turbo-Q8_0.gguf already exists — skipping."
    fi

    # Flux.2 Klein 4B (image editing)
    if [[ ! -f "$DIFF_DIR/flux-2-klein-4b-Q8_0.gguf" ]]; then
        info "Downloading flux-2-klein-4b-Q8_0.gguf (editing)..."
        HF_DOWNLOAD "bartowski/FLUX.2-Klein-dev-GGUF" \
            "flux-2-klein-4b-Q8_0.gguf" "$DIFF_DIR"
    else
        warn "flux-2-klein-4b-Q8_0.gguf already exists — skipping."
    fi

    # VAE (for generation)
    if [[ ! -f "$VAE_DIR/ae.safetensors" ]]; then
        info "Downloading ae.safetensors (VAE for generation)..."
        HF_DOWNLOAD "bartowski/Z-Image-Turbo-GGUF" \
            "ae.safetensors" "$VAE_DIR"
    else
        warn "ae.safetensors already exists — skipping."
    fi

    # VAE (for editing)
    if [[ ! -f "$VAE_DIR/flux2_ae.safetensors" ]]; then
        info "Downloading flux2_ae.safetensors (VAE for editing)..."
        HF_DOWNLOAD "bartowski/FLUX.2-dev-GGUF" \
            "flux2_ae.safetensors" "$VAE_DIR"
    else
        warn "flux2_ae.safetensors already exists — skipping."
    fi

    # Text encoder (shared)
    if [[ ! -f "$TXT_DIR/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" ]]; then
        info "Downloading Qwen3-4B-Instruct-2507-Q4_K_M.gguf (text encoder)..."
        HF_DOWNLOAD "bartowski/Qwen3-4B-Instruct-2507-GGUF" \
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf" "$TXT_DIR"
    else
        warn "Qwen3-4B-Instruct-2507-Q4_K_M.gguf already exists — skipping."
    fi
}

download_tts_models() {
    info "Downloading TTS (Piper) models..."
    local TTS_DIR="services/piper/models"
    mkdir -p "$TTS_DIR"
    # English
    if [[ ! -f "$TTS_DIR/en_US-lessac-medium.onnx" ]]; then
        info "Downloading en_US-lessac-medium..."
        HF_DOWNLOAD "rhasspy/piper-voices" \
            "en/en_US/lessac/medium/en_US-lessac-medium.onnx" "$TTS_DIR"
    fi
    # Russian
    if [[ ! -f "$TTS_DIR/ru_RU-ruslan-medium.onnx" ]]; then
        info "Downloading ru_RU-ruslan-medium..."
        HF_DOWNLOAD "rhasspy/piper-voices" \
            "ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx" "$TTS_DIR"
    fi
    # Config
    if [[ ! -f "$TTS_DIR/config.json" ]]; then
        HF_DOWNLOAD "rhasspy/piper-voices" "config.json" "$TTS_DIR"
    fi
}

# ── Build & Launch ──
build_and_launch() {
    local PROFILE="--profile with-image-gen"
    [[ "$WITH_VOICE" == "true" ]] && PROFILE="$PROFILE --profile with-voice"
    [[ "$WITH_RAG" == "true" ]]    && PROFILE="$PROFILE --profile with-rag"

    # Remove any previous CPU override so it doesn't interfere
    rm -f docker-compose.override.yml

    if ! command -v nvidia-smi &>/dev/null || ! nvidia-smi -L &>/dev/null; then
        warn "No GPU detected — generating CPU override."
        generate_cpu_override
    else
        info "GPU detected — using default GPU images."
    fi

    info "Building Docker images..."
    docker compose -f docker-compose.all.yml $PROFILE build

    info "Starting services..."
    docker compose -f docker-compose.all.yml $PROFILE up -d

    info "Waiting for services to start..."
    sleep 10

    local STATUS
    STATUS=$(curl -s http://localhost:5000/health 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unreachable")
    if [[ "$STATUS" == "ok" ]]; then
        info "FLAI v8.0 is running! Open http://localhost:5000 in your browser."
    else
        warn "Health check returned: $STATUS — check 'docker compose logs' for details."
    fi
}

# ── Tests ──
run_tests() {
    info "Running unit tests..."
    pip install -r requirements.txt -q 2>/dev/null
    pytest --tb=short -q 2>&1 || warn "Some tests failed — check output above."
}

# ── Usage ──
usage() {
    cat <<'USAGE'
FLAI v8.0 — Deployment Script

Usage: ./deploy.sh [OPTIONS]

Options:
  --with-voice        Deploy Whisper ASR + Piper TTS
  --with-rag          Deploy Qdrant for RAG (document search)
  --with-image-gen    Deploy stable-diffusion.cpp for image generation/editing
  --download-models   Download GGUF/safetensors models from HuggingFace
  --run-tests         Run unit tests after deployment
  --help, -h          Show this help message

Model Download Sizes (approximate):
  llama.cpp:
    Qwen3-4B-Instruct (chat)         ~2.5 GB
    gpt-oss-20b (reasoning)          ~12 GB
    Qwen3VL-8B (multimodal)          ~5.5 GB
    bge-m3 (embeddings)              ~2.2 GB
  Image generation (Z-Image Turbo)   ~6.5 GB
  Image editing (Flux.2 Klein 4B)    ~5 GB
  TTS (Piper)                        ~0.2 GB
USAGE
}

# ── Parse arguments ──
WITH_VOICE=false
WITH_RAG=false
DOWNLOAD_MODELS=false
RUN_TESTS=false

for arg in "$@"; do
    case "$arg" in
        --with-voice)     WITH_VOICE=true ;;
        --with-rag)       WITH_RAG=true ;;
        --download-models) DOWNLOAD_MODELS=true ;;
        --run-tests)      RUN_TESTS=true ;;
        --help|-h)        usage; exit 0 ;;
    esac
done

# ── Main ──
main() {
    echo "============================================"
    echo "  FLAI v8.0 — Deployment Script"
    echo "============================================"
    echo ""

    check_prereqs
    setup_env

    if [[ "$DOWNLOAD_MODELS" == "true" ]]; then
        download_llamacpp_models
        download_sd_cpp_models
        download_tts_models
    fi

    build_and_launch

    if [[ "$RUN_TESTS" == "true" ]]; then
        run_tests
    fi

    echo ""
    echo "============================================"
    info "Deployment complete!"
    echo "============================================"
    echo "  Web UI:   http://localhost:5000"
    echo "  Health:   http://localhost:5000/health"
    echo "  Logs:     docker compose -f docker-compose.all.yml logs -f"
    echo "  Stop:     docker compose -f docker-compose.all.yml --profile with-image-gen down"
    echo "============================================"
}

main "$@"