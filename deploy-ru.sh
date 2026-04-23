#!/usr/bin/env bash
# FLAI v8.0 — Скрипт развёртывания на одном сервере

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Цвета ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Проверка зависимостей ──
check_prereqs() {
    info "Проверка зависимостей..."
    command -v docker &>/dev/null     || error "Docker не установлен. Установите Docker."
    command -v docker compose &>/dev/null || error "Плагин Docker Compose не установлен."
    command -v git &>/dev/null        || error "Git не установлен."
    if ! command -v nvidia-smi &>/dev/null; then
        warn "Видеокарта NVIDIA не обнаружена — ускорение на GPU будет недоступно."
    fi
    info "Зависимости проверены."
}

# ── Настройка окружения ──
setup_env() {
    if [[ -f .env ]]; then
        warn ".env уже существует — пропускаем настройку."
        return
    fi
    info "Создаю .env из .env.example..."
    cp .env.example .env
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET/" .env
    info ".env создан. Отредактируйте его для настройки моделей, URL и параметров."
}

# ── Генерация docker-compose.override.yml для режима CPU ──
generate_cpu_override() {
    cat > docker-compose.override.yml <<'EOF'
# Автоматически сгенерированный override для режима CPU.
# Заменяет GPU-сервисы на образы, оптимизированные для процессора.
services:
  llamacpp:
    image: ghcr.io/ggml-org/llama.cpp:server
    # Явно указываем стандартный рантайм (runc) вместо nvidia
    runtime: "runc"
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
      # Принудительно используем CPU Dockerfile
      dockerfile: Dockerfile.sd_cpp-cpu
    runtime: "runc"
    environment: {}
    deploy:
      resources:
        limits:
          cpus: '8.0'
          memory: 24G
        reservations:
          cpus: '4.0'
          memory: 16G
    entrypoint: ["python3", "/app/sd_wrapper.py"]
EOF
    info "Создан docker-compose.override.yml для режима CPU."
}

# ── Вспомогательная функция скачивания моделей ──
HF_DOWNLOAD() {
    local repo="$1" file="$2" dest="$3"
    if command -v huggingface-cli &>/dev/null; then
        huggingface-cli download "$repo" "$file" --local-dir "$dest" 2>&1
    else
        local url="https://huggingface.co/$repo/resolve/main/$file"
        info "Скачиваю $file из HuggingFace..."
        mkdir -p "$dest"
        curl -L -o "$dest/$file" "$url"
    fi
}

# ── Модели llama.cpp ──
download_llamacpp_models() {
    info "Скачиваю модели llama.cpp..."
    local MODEL_DIR="services/llamacpp/models"

    # Чат-модель
    if [[ ! -f "$MODEL_DIR/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" ]]; then
        info "Скачиваю Qwen3-4B-Instruct-2507-Q4_K_M.gguf (чат)..."
        HF_DOWNLOAD "bartowski/Qwen3-4B-Instruct-2507-GGUF" \
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf" "$MODEL_DIR"
    else
        warn "Qwen3-4B-Instruct-2507-Q4_K_M.gguf уже есть — пропускаю."
    fi

    # Модель рассуждений (сложные задачи)
    if [[ ! -f "$MODEL_DIR/gpt-oss-20b-mxfp4.gguf" ]]; then
        info "Скачиваю gpt-oss-20b-mxfp4.gguf (рассуждения)..."
        HF_DOWNLOAD "openai/gpt-oss-20b-GGUF" \
            "gpt-oss-20b-mxfp4.gguf" "$MODEL_DIR"
    else
        warn "gpt-oss-20b-mxfp4.gguf уже есть — пропускаю."
    fi

    # Мультимодальная модель (с mmproj)
    if [[ ! -d "$MODEL_DIR/Qwen3VL-8B-Instruct-Q4_K_M" ]]; then
        info "Скачиваю Qwen3VL-8B-Instruct-Q4_K_M (мультимодальная)..."
        mkdir -p "$MODEL_DIR/Qwen3VL-8B-Instruct-Q4_K_M"
        HF_DOWNLOAD "bartowski/Qwen3VL-8B-Instruct-GGUF" \
            "Qwen3VL-8B-Instruct-Q4_K_M.gguf" "$MODEL_DIR/Qwen3VL-8B-Instruct-Q4_K_M"
        HF_DOWNLOAD "bartowski/Qwen3VL-8B-Instruct-GGUF" \
            "mmproj-F16.gguf" "$MODEL_DIR/Qwen3VL-8B-Instruct-Q4_K_M"
    else
        warn "Qwen3VL-8B-Instruct-Q4_K_M уже есть — пропускаю."
    fi

    # Модель эмбеддингов (для RAG)
    if [[ ! -f "$MODEL_DIR/bge-m3-Q8_0.gguf" ]]; then
        info "Скачиваю bge-m3-Q8_0.gguf (эмбеддинги)..."
        HF_DOWNLOAD "bartowski/bge-m3-GGUF" \
            "bge-m3-Q8_0.gguf" "$MODEL_DIR"
    else
        warn "bge-m3-Q8_0.gguf уже есть — пропускаю."
    fi
}

# ── Модели Stable Diffusion ──
download_sd_cpp_models() {
    info "Скачиваю модели stable-diffusion.cpp..."
    local DIFF_DIR="services/sd_cpp/models/diffusion_models"
    local VAE_DIR="services/sd_cpp/models/vae"
    local TXT_DIR="services/sd_cpp/models/text_encoders"

    # Z-Image Turbo (генерация изображений)
    if [[ ! -f "$DIFF_DIR/z_image_turbo-Q8_0.gguf" ]]; then
        info "Скачиваю z_image_turbo-Q8_0.gguf..."
        HF_DOWNLOAD "bartowski/Z-Image-Turbo-GGUF" \
            "z_image_turbo-Q8_0.gguf" "$DIFF_DIR"
    else
        warn "z_image_turbo-Q8_0.gguf уже есть — пропускаю."
    fi

    # Flux.2 Klein 4B (редактирование изображений)
    if [[ ! -f "$DIFF_DIR/flux-2-klein-4b-Q8_0.gguf" ]]; then
        info "Скачиваю flux-2-klein-4b-Q8_0.gguf (редактирование)..."
        HF_DOWNLOAD "bartowski/FLUX.2-Klein-dev-GGUF" \
            "flux-2-klein-4b-Q8_0.gguf" "$DIFF_DIR"
    else
        warn "flux-2-klein-4b-Q8_0.gguf уже есть — пропускаю."
    fi

    # VAE (для генерации)
    if [[ ! -f "$VAE_DIR/ae.safetensors" ]]; then
        info "Скачиваю ae.safetensors (VAE для генерации)..."
        HF_DOWNLOAD "bartowski/Z-Image-Turbo-GGUF" \
            "ae.safetensors" "$VAE_DIR"
    else
        warn "ae.safetensors уже есть — пропускаю."
    fi

    # VAE (для редактирования)
    if [[ ! -f "$VAE_DIR/flux2_ae.safetensors" ]]; then
        info "Скачиваю flux2_ae.safetensors (VAE для редактирования)..."
        HF_DOWNLOAD "bartowski/FLUX.2-dev-GGUF" \
            "flux2_ae.safetensors" "$VAE_DIR"
    else
        warn "flux2_ae.safetensors уже есть — пропускаю."
    fi

    # Кодировщик текста (общий)
    if [[ ! -f "$TXT_DIR/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" ]]; then
        info "Скачиваю Qwen3-4B-Instruct-2507-Q4_K_M.gguf (кодировщик текста)..."
        HF_DOWNLOAD "bartowski/Qwen3-4B-Instruct-2507-GGUF" \
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf" "$TXT_DIR"
    else
        warn "Qwen3-4B-Instruct-2507-Q4_K_M.gguf уже есть — пропускаю."
    fi
}

# ── Модели TTS (синтез речи) ──
download_tts_models() {
    info "Скачиваю модели TTS (Piper)..."
    local TTS_DIR="services/piper/models"
    mkdir -p "$TTS_DIR"
    # Английский
    if [[ ! -f "$TTS_DIR/en_US-lessac-medium.onnx" ]]; then
        info "Скачиваю en_US-lessac-medium..."
        HF_DOWNLOAD "rhasspy/piper-voices" \
            "en/en_US/lessac/medium/en_US-lessac-medium.onnx" "$TTS_DIR"
    fi
    # Русский
    if [[ ! -f "$TTS_DIR/ru_RU-ruslan-medium.onnx" ]]; then
        info "Скачиваю ru_RU-ruslan-medium..."
        HF_DOWNLOAD "rhasspy/piper-voices" \
            "ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx" "$TTS_DIR"
    fi
    # Конфигурация
    if [[ ! -f "$TTS_DIR/config.json" ]]; then
        HF_DOWNLOAD "rhasspy/piper-voices" "config.json" "$TTS_DIR"
    fi
}

# ── Сборка и запуск ──
build_and_launch() {
    local PROFILE="--profile with-image-gen"
    [[ "$WITH_VOICE" == "true" ]] && PROFILE="$PROFILE --profile with-voice"
    [[ "$WITH_RAG" == "true" ]]    && PROFILE="$PROFILE --profile with-rag"

    # Удаляем предыдущий override, чтобы не мешал
    rm -f docker-compose.override.yml

    if ! command -v nvidia-smi &>/dev/null || ! nvidia-smi -L &>/dev/null; then
        warn "GPU не обнаружен — создаю override для CPU."
        generate_cpu_override
    else
        info "GPU обнаружен — используются стандартные образы."
    fi

    # Удаляем старые контейнеры во избежание конфликтов
    info "Останавливаю старые контейнеры (если есть)..."
    docker compose -f docker-compose.all.yml $PROFILE down --remove-orphans 2>/dev/null || true

    # Формируем список файлов композиции
    COMPOSE_FILES=("-f" "docker-compose.all.yml")
    if [[ -f docker-compose.override.yml ]]; then
        COMPOSE_FILES+=("-f" "docker-compose.override.yml")
    fi

    info "Собираю Docker-образы..."
    docker compose "${COMPOSE_FILES[@]}" $PROFILE build

    info "Запускаю сервисы..."
    docker compose "${COMPOSE_FILES[@]}" $PROFILE up -d

    info "Ожидаю запуск сервисов..."
    sleep 10

    local STATUS
    STATUS=$(curl -s http://localhost:5000/health 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "недоступен")
    if [[ "$STATUS" == "ok" ]]; then
        info "FLAI v8.0 запущен! Откройте http://localhost:5000 в браузере."
    else
        warn "Проверка здоровья: $STATUS — проверьте 'docker compose logs' для деталей."
    fi
}

# ── Тесты ──
run_tests() {
    info "Запускаю тесты..."
    pip install -r requirements.txt -q 2>/dev/null
    pytest --tb=short -q 2>&1 || warn "Некоторые тесты не прошли — проверьте вывод выше."
}

# ── Справка ──
usage() {
    cat <<'USAGE'
FLAI v8.0 — Скрипт развёртывания

Использование: ./deploy-ru.sh [ОПЦИИ]

Опции:
  --with-voice        Развернуть Whisper ASR + Piper TTS
  --with-rag          Развернуть Qdrant для RAG (поиск по документам)
  --with-image-gen    Развернуть stable-diffusion.cpp для генерации/редактирования
  --download-models   Скачать GGUF/safetensors модели из HuggingFace
  --run-tests         Запустить тесты после развёртывания
  --help, -h          Показать эту справку

Размеры скачиваемых моделей (примерно):
  llama.cpp:
    Qwen3-4B-Instruct (чат)            ~2,5 ГБ
    gpt-oss-20b (рассуждения)          ~12 ГБ
    Qwen3VL-8B (мультимодальная)       ~5,5 ГБ
    bge-m3 (эмбеддинги)                ~2,2 ГБ
  Генерация изображений (Z-Image Turbo) ~6,5 ГБ
  Редактирование (Flux.2 Klein 4B)    ~5 ГБ
  TTS (Piper)                         ~0,2 ГБ
USAGE
}

# ── Разбор аргументов ──
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

# ── Основной запуск ──
main() {
    echo "============================================"
    echo "  FLAI v8.0 — Скрипт развёртывания"
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
    info "Развёртывание завершено!"
    echo "============================================"
    echo "  Веб-интерфейс: http://localhost:5000"
    echo "  Здоровье:      http://localhost:5000/health"
    echo "  Логи:          docker compose -f docker-compose.all.yml logs -f"
    echo "  Остановка:     docker compose -f docker-compose.all.yml --profile with-image-gen down"
    echo "============================================"
}

main "$@"