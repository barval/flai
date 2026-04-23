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



# ── Вспомогательная функция скачивания моделей ──
HF_DOWNLOAD() {
    local repo="$1" file="$2" dest="$3"
    local url="https://huggingface.co/$repo/resolve/main/$file"
    info "Скачиваю $file из HuggingFace..."
    local dir="$(dirname "$dest")"
    mkdir -p "$dir"
    
    # Retry download up to 3 times
    for attempt in 1 2 3; do
        curl -L --progress-bar -o "$dest" "$url"
        
        # Check if file is valid (more than 1KB)
        local size=$(stat -c%s "$dest" 2>/dev/null || echo "0")
        if [[ "$size" -gt 1024 ]]; then
            info "Успешно скачано: $file ($size bytes)"
            return 0
        else
            warn "Попытка $attempt не удалась, повторяю..."
            rm -f "$dest"
            sleep 2
        fi
    done
    error "Не удалось скачать $file после 3 попыток"
}

# ── Модели llama.cpp ──
download_llamacpp_models() {
    info "Скачиваю модели llama.cpp..."
    local MODEL_DIR="services/llamacpp/models"

    # Чат-модель (публичный репозиторий - Instruct)
    if [[ ! -f "$MODEL_DIR/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" ]]; then
        info "Скачиваю Qwen3-4B-Instruct-2507-Q4_K_M.gguf (чат)..."
        HF_DOWNLOAD "unsloth/Qwen3-4B-Instruct-2507-GGUF" \
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf" \
            "$MODEL_DIR/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    else
        warn "Qwen3-4B-Instruct-2507-Q4_K_M.gguf уже есть — пропускаю."
    fi

    # Модель рассуждений (DeepSeek R1)
    if [[ ! -d "$MODEL_DIR/DeepSeek-R1-Q4_K_M" ]]; then
        info "Скачиваю DeepSeek-R1-Q4_K_M (рассуждения)..."
        mkdir -p "$MODEL_DIR/DeepSeek-R1-Q4_K_M"
        HF_DOWNLOAD "unsloth/DeepSeek-R1-GGUF" \
            "DeepSeek-R1-Q4_K_M/DeepSeek-R1-Q4_K_M-00001-of-00009.gguf" \
            "$MODEL_DIR/DeepSeek-R1-Q4_K_M/DeepSeek-R1-Q4_K_M-00001-of-00009.gguf"
    else
        warn "DeepSeek-R1-Q4_K_M уже есть — пропускаю."
    fi

    # Мультимодальная модель (публичный репозиторий)
    if [[ ! -d "$MODEL_DIR/Qwen3VL-4B-Instruct-Q4_K_M" ]]; then
        info "Скачиваю Qwen3VL-4B-Instruct-Q4_K_M (мультимодальная)..."
        mkdir -p "$MODEL_DIR/Qwen3VL-4B-Instruct-Q4_K_M"
        HF_DOWNLOAD "Qwen/Qwen3-VL-4B-Instruct-GGUF" \
            "Qwen3-VL-4B-Instruct-Q4_K_M.gguf" \
            "$MODEL_DIR/Qwen3VL-4B-Instruct-Q4_K_M/Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
        HF_DOWNLOAD "Qwen/Qwen3-VL-4B-Instruct-GGUF" \
            "mmproj-Qwen3VL-4B-Instruct-F16.gguf" \
            "$MODEL_DIR/Qwen3VL-4B-Instruct-Q4_K_M/mmproj-F16.gguf"
    else
        warn "Qwen3VL-4B-Instruct-Q4_K_M уже есть — пропускаю."
    fi

    # Модель эмбеддингов (публичный репозиторий)
    if [[ ! -f "$MODEL_DIR/bge-m3-Q8_0.gguf" ]]; then
        info "Скачиваю bge-m3-Q8_0.gguf (эмбеддинги)..."
        HF_DOWNLOAD "gpustack/bge-m3-GGUF" \
            "bge-m3-Q8_0.gguf" \
            "$MODEL_DIR/bge-m3-Q8_0.gguf"
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

    local HAS_GPU=false
    if command -v nvidia-smi &>/dev/null && nvidia-smi -L 2>/dev/null | grep -q GPU; then
        HAS_GPU=true
    fi

    if [[ "$HAS_GPU" == "true" ]]; then
        COMPOSE_FILE="docker-compose.gpu.yml"
        info "GPU обнаружен — используется GPU compose файл."
    else
        COMPOSE_FILE="docker-compose.cpu.yml"
        warn "GPU не обнаружен — используется CPU compose файл."
    fi

    # Удаляем старые контейнеры во избежание конфликтов
    info "Останавливаю старые контейнеры (если есть)..."
    docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true

    info "Собираю Docker-образы..."
    docker compose -f "$COMPOSE_FILE" $PROFILE build

    info "Запускаю сервисы..."
    docker compose -f "$COMPOSE_FILE" $PROFILE up -d

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
    echo "  Логи:          docker compose -f docker-compose.gpu.yml logs -f"
    echo "  Остановка:     docker compose -f docker-compose.gpu.yml --profile with-image-gen down"
    echo "============================================"
}

main "$@"