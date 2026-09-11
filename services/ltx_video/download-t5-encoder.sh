#!/bin/bash
# services/ltx_video/download-t5-encoder.sh
# Download T5 text encoder (PixArt T5) for LTX-Video offline use.
#
# Tries in order:
#   1. Docker + huggingface_hub (python:3.11-slim)
#   2. git lfs clone
#   3. Manual download with curl (prints instructions for each file)
#
# Target structure (mounted into container at /app/models/t5_encoder):
#   services/ltx_video/models/t5_encoder/
#   ├── text_encoder/
#   │   ├── config.json
#   │   ├── model.safetensors.index.json
#   │   ├── model-00001-of-00002.safetensors  (~2.4 GB)
#   │   └── model-00002-of-00002.safetensors  (~2.4 GB)
#   └── tokenizer/
#       ├── added_tokens.json
#       ├── special_tokens_map.json
#       ├── spiece.model
#       └── tokenizer_config.json
#
# Accepts LANG=ru for Russian messages.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$SCRIPT_DIR/models/t5_encoder"
HF_REPO="PixArt-alpha/PixArt-XL-2-1024-MS"
HF_BASE="https://huggingface.co/$HF_REPO/resolve/main"
REPO_URL="https://huggingface.co/$HF_REPO"
LANG="${LANG:-en}"

msg() {
    local en="$1" ru="$2"
    if [ "$LANG" = "ru" ]; then echo "$ru"; else echo "$en"; fi
}

# Files needed
declare -A FILES
FILES["text_encoder/config.json"]=""
FILES["text_encoder/model.safetensors.index.json"]=""
FILES["text_encoder/model-00001-of-00002.safetensors"]="~2.4 GB"
FILES["text_encoder/model-00002-of-00002.safetensors"]="~2.4 GB"
FILES["tokenizer/added_tokens.json"]=""
FILES["tokenizer/special_tokens_map.json"]=""
FILES["tokenizer/spiece.model"]=""
FILES["tokenizer/tokenizer_config.json"]=""

# ── helpers ─────────────────────────────────────────────────────

print_structure() {
    echo ""
    msg "Expected final structure:" "Ожидаемая структура:"
    echo "  $TARGET_DIR/"
    echo "  ├── model_index.json (optional, for transformers)"
    echo "  ├── text_encoder/"
    echo "  │   ├── config.json"
    echo "  │   ├── model.safetensors.index.json"
    echo "  │   ├── model-00001-of-00002.safetensors"
    echo "  │   └── model-00002-of-00002.safetensors"
    echo "  └── tokenizer/"
    echo "      ├── added_tokens.json"
    echo "      ├── special_tokens_map.json"
    echo "      ├── spiece.model"
    echo "      └── tokenizer_config.json"
}

print_success() {
    echo ""
    msg \
        "=== T5 encoder downloaded successfully to: $TARGET_DIR ===" \
        "=== T5 encoder успешно скачан в: $TARGET_DIR ==="
    du -sh "$TARGET_DIR" 2>/dev/null || true
    print_structure
}

# ── Method 1: Docker + huggingface_hub ─────────────────────────

try_docker_download() {
    if ! command -v docker &>/dev/null; then
        msg "Docker is not available — trying next method." \
            "Docker недоступен — пробую другой метод."
        return 1
    fi

    if [ -f "$TARGET_DIR/text_encoder/config.json" ] && \
       [ -f "$TARGET_DIR/text_encoder/model-00001-of-00002.safetensors" ]; then
        print_success
        exit 0
    fi

    local HF_CACHE="${HF_HOME:-/tmp/hf_cache_ltx}"
    mkdir -p "$HF_CACHE" "$TARGET_DIR"

    msg \
        "--- Method 1: Docker + huggingface_hub ---" \
        "--- Метод 1: Docker + huggingface_hub ---"

    docker run --rm \
        -v "$TARGET_DIR:/app/models/t5_encoder" \
        python:3.11-slim \
        bash -c "
pip install -q huggingface_hub && hf download \
    PixArt-alpha/PixArt-XL-2-1024-MS \
    --local-dir /app/models/t5_encoder \
    --include 'text_encoder/*' \
    --include 'tokenizer/*' \
    --resume-download
"

    if [ $? -eq 0 ] && [ -f "$TARGET_DIR/text_encoder/config.json" ]; then
        print_success
        exit 0
    fi

    msg \
        "Docker download failed — trying next method." \
        "Скачивание через Docker не удалось — пробую другой метод."
    return 1
}

# ── Method 2: git lfs ───────────────────────────────────────────

try_git_lfs() {
    if ! command -v git-lfs &>/dev/null && ! git lfs version &>/dev/null 2>&1; then
        echo ""
        msg \
            "--- git-lfs not installed ---" \
            "--- git-lfs не установлен ---"
        msg \
            "Install: sudo apt install git-lfs && git lfs install" \
            "Установите: sudo apt install git-lfs && git lfs install"
        msg \
            "Then re-run this script, or use the manual method below." \
            "Затем перезапустите скрипт или используйте ручной метод."
        return 1
    fi

    echo ""
    msg \
        "--- Method 2: git lfs ---" \
        "--- Метод 2: git lfs ---"
    echo ""

    local GIT_DIR="$TARGET_DIR"
    rm -rf "$GIT_DIR" 2>/dev/null || true

    msg \
        "Cloning repo (without LFS files)..." \
        "Клонирую репозиторий (без LFS файлов)..."
    GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 "$REPO_URL" "$GIT_DIR" 2>&1 | tail -3
    cd "$GIT_DIR"

    msg \
        "Pulling only text_encoder/ and tokenizer/..." \
        "Скачиваю только text_encoder/ и tokenizer/..."
    git lfs pull --include="text_encoder/*" --include="tokenizer/*" 2>&1 | tail -5

    cd "$SCRIPT_DIR"

    if [ -f "$TARGET_DIR/text_encoder/config.json" ] && \
       [ -f "$TARGET_DIR/text_encoder/model-00001-of-00002.safetensors" ]; then
        print_success
        exit 0
    fi

    msg \
        "git lfs pull may not have completed. Check files above." \
        "git lfs pull возможно не завершился. Проверьте файлы выше."
    return 1
}

# ── Method 3: manual curl instructions ──────────────────────────

print_manual_instructions() {
    echo ""
    msg \
        "================================================================" \
        "================================================================"
    msg \
        "  Automatic download failed." \
        "  Автоматическое скачивание не удалось."
    msg \
        "  Download the files manually." \
        "  Скачайте файлы вручную."
    msg \
        "================================================================" \
        "================================================================"
    echo ""
    msg "Target directory:" "Целевая директория:"
    echo "  $TARGET_DIR"
    echo ""

    for file in "${!FILES[@]}"; do
        size="${FILES[$file]}"
        url="$HF_BASE/$file"
        target_path="$TARGET_DIR/$file"

        echo "  $file$([ -n "$size" ] && echo "  ($size)")"
        echo "    → $url"
        echo "    → $target_path"
        echo ""
    done

    msg \
        "================================================================" \
        "================================================================"
    msg \
        "  Option A — curl (one command per file):" \
        "  Вариант A — curl (по одной команде на файл):"
    echo ""
    for file in "${!FILES[@]}"; do
        url="$HF_BASE/$file"
        target_path="$TARGET_DIR/$file"
        echo "  mkdir -p \"$(dirname "$target_path")\" \\"
        echo "    && curl -L --retry 3 -o \"$target_path\" \"$url\""
    done
    echo ""
    msg \
        "  Option B — git lfs (full clone, auto-retry):" \
        "  Вариант B — git lfs (полный клон, авто-повтор):"
    echo ""
    echo "  GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 \\"
    echo "    $REPO_URL \\"
    echo "    $TARGET_DIR"
    echo "  cd $TARGET_DIR"
    echo "  git lfs pull --include=\"text_encoder/*\" --include=\"tokenizer/*\""
    echo "  cd $SCRIPT_DIR"
    echo ""
    msg \
        "  (requires: sudo apt install git-lfs && git lfs install)" \
        "  (требуется: sudo apt install git-lfs && git lfs install)"
    echo "================================================================"
    print_structure
    exit 1
}

# ── main ─────────────────────────────────────────────────────────

mkdir -p "$TARGET_DIR"

msg \
    "Downloading T5 text encoder ($HF_REPO)..." \
    "Скачиваю T5 text encoder ($HF_REPO)..."
echo "Target: $TARGET_DIR"
echo ""

try_docker_download || try_git_lfs || print_manual_instructions