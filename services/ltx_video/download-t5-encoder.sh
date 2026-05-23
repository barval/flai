#!/bin/bash
# services/ltx_video/download-t5-encoder.sh
# Download T5 text encoder (PixArt T5) for LTX-Video offline use.
#
# Tries in order:
#   1. huggingface_hub snapshot_download
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

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$SCRIPT_DIR/models/t5_encoder"
HF_REPO="PixArt-alpha/PixArt-XL-2-1024-MS"
HF_BASE="https://huggingface.co/$HF_REPO/resolve/main"
REPO_URL="https://huggingface.co/$HF_REPO"

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
    echo "Expected final structure:"
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
    echo "=== T5 encoder downloaded successfully to: $TARGET_DIR ==="
    du -sh "$TARGET_DIR" 2>/dev/null || true
    print_structure
}

# ── Method 1: snapshot_download ─────────────────────────────────

try_snapshot_download() {
    if ! python3 -c "import huggingface_hub" 2>/dev/null; then
        return 1
    fi

    echo "--- Method 1: huggingface_hub snapshot_download ---"
    echo ""

    local HF_CACHE="${HF_HOME:-/tmp/hf_cache_ltx}"
    mkdir -p "$HF_CACHE"

    env TARGET_DIR="$TARGET_DIR" HF_HOME="$HF_CACHE" python3 << 'PYEOF'
import os
import sys
target = os.environ["TARGET_DIR"]
cache = os.environ["HF_HOME"]
print(f"HF cache: {cache}")
print(f"Downloading to: {target}")
print("This may take a while (~4.8 GB)...")
try:
    from huggingface_hub import snapshot_download
    snapshot_download(
        repo_id="PixArt-alpha/PixArt-XL-2-1024-MS",
        local_dir=target,
        allow_patterns=["text_encoder/*", "tokenizer/*"],
        max_workers=1,
    )
    print("Download complete!")
    sys.exit(0)
except Exception as e:
    print(f"snapshot_download failed: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

    if [ $? -eq 0 ]; then
        print_success
        exit 0
    fi

    echo "snapshot_download failed — trying next method."
    return 1
}

# ── Method 2: git lfs ───────────────────────────────────────────

try_git_lfs() {
    if ! command -v git-lfs &>/dev/null && ! git lfs version &>/dev/null 2>&1; then
        echo ""
        echo "--- git-lfs not installed ---"
        echo "Install: sudo apt install git-lfs && git lfs install"
        echo "Then re-run this script, or use the manual method below."
        return 1
    fi

    echo ""
    echo "--- Method 2: git lfs ---"
    echo ""

    local GIT_DIR="$TARGET_DIR"
    rm -rf "$GIT_DIR" 2>/dev/null || true

    echo "Cloning repo (without LFS files)..."
    GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 "$REPO_URL" "$GIT_DIR"
    cd "$GIT_DIR"

    echo ""
    echo "Pulling only text_encoder/ and tokenizer/..."
    git lfs pull --include="text_encoder/*" --include="tokenizer/*"

    cd "$SCRIPT_DIR"

    # Verify key files exist
    if [ -f "$TARGET_DIR/text_encoder/config.json" ] && \
       [ -f "$TARGET_DIR/text_encoder/model-00001-of-00002.safetensors" ]; then
        print_success
        exit 0
    fi

    echo "git lfs pull may not have completed. Check files above."
    return 1
}

# ── Method 3: manual curl instructions ──────────────────────────

print_manual_instructions() {
    echo ""
    echo "================================================================"
    echo "  Automatic download failed."
    echo "  Download the files manually."
    echo "================================================================"
    echo ""
    echo "Target directory: $TARGET_DIR"
    echo ""

    for file in "${!FILES[@]}"; do
        size="${FILES[$file]}"
        url="$HF_BASE/$file"
        target_path="$TARGET_DIR/$file"

        if [ -n "$size" ]; then
            echo "  $file  ($size)"
        else
            echo "  $file"
        fi
        echo "    → $url"
        echo "    → $target_path"
        echo ""
    done

    echo "================================================================"
    echo "  Option A — curl (one command per file):"
    echo ""
    for file in "${!FILES[@]}"; do
        url="$HF_BASE/$file"
        target_path="$TARGET_DIR/$file"
        echo "  mkdir -p \"$(dirname "$target_path")\" \\"
        echo "    && curl -L --retry 3 -o \"$target_path\" \"$url\""
    done
    echo ""
    echo "  Option B — git lfs (full clone, auto-retry):"
    echo ""
    echo "  GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 \\"
    echo "    $REPO_URL \\"
    echo "    $TARGET_DIR"
    echo "  cd $TARGET_DIR"
    echo "  git lfs pull --include=\"text_encoder/*\" --include=\"tokenizer/*\""
    echo "  cd $SCRIPT_DIR"
    echo ""
    echo "  (requires: sudo apt install git-lfs && git lfs install)"
    echo "================================================================"
    print_structure
    exit 1
}

# ── main ─────────────────────────────────────────────────────────

mkdir -p "$TARGET_DIR"

echo "Downloading T5 text encoder ($HF_REPO)..."
echo "Target: $TARGET_DIR"
echo ""

try_snapshot_download
try_git_lfs
print_manual_instructions
