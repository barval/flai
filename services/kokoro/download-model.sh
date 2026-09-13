#!/bin/bash
# services/kokoro/download-model.sh
# Download Kokoro-82M model weights and voice packs

set -e

# Always operate inside services/kokoro/ regardless of caller's cwd —
# relative paths below (./models, config.json) land in the build context.
cd "$(dirname "$0")"

MODELS_DIR="./models"
VOICES_DIR="./voices"
mkdir -p "$MODELS_DIR" "$VOICES_DIR"

# Use dockerized python for huggingface_hub downloads
check_docker() {
    if command -v docker &>/dev/null; then
        return 0
    fi
    echo "Docker not found. Install it or manually download from HuggingFace."
    echo "  - https://huggingface.co/hexgrad/Kokoro-82M"
    echo "  - https://huggingface.co/zaakirio/kokoro-ru"
    return 1
}

download_hf() {
    local repo="$1"
    local file="$2"
    local dest="$3"
    echo "Downloading $file from $repo..."
    docker run --rm -v "$(pwd):/app" python:3.11-slim bash -c "
        pip install -q huggingface_hub && \
        python3 -c \"
from huggingface_hub import hf_hub_download
import os, sys
dest = hf_hub_download('$repo', '$file')
os.system('cp \\\"' + dest + '\\\" \\\"/app/$dest\\\"')
print('Done: $file')
\""
}

# --- Model checkpoints -----------------------------------------

# Russian base checkpoint (for sveta female voice)
if [ ! -f "$MODELS_DIR/kokoro-ru-v2-base.pth" ]; then
    download_hf "zaakirio/kokoro-ru" "kokoro-ru-v2-base.pth" "$MODELS_DIR/kokoro-ru-v2-base.pth"
else
    echo "Base checkpoint exists, skipping"
fi

# Russian male checkpoint (dima)
if [ ! -f "$MODELS_DIR/kokoro-ru-v2-dima.pth" ]; then
    download_hf "zaakirio/kokoro-ru" "kokoro-ru-v2-dima.pth" "$MODELS_DIR/kokoro-ru-v2-dima.pth"
else
    echo "Dima checkpoint exists, skipping"
fi

# --- Voice packs -----------------------------------------------

# Russian voices (zaakirio/kokoro-ru)
for voice in sveta dima; do
    if [ ! -f "$VOICES_DIR/$voice.pt" ]; then
        download_hf "zaakirio/kokoro-ru" "voices/$voice.pt" "$VOICES_DIR/$voice.pt"
    else
        echo "Voice $voice exists, skipping"
    fi
done

# English voices (hexgrad/Kokoro-82M)
# af_heart - best US female (A-grade)
if [ ! -f "$VOICES_DIR/af_heart.pt" ]; then
    download_hf "hexgrad/Kokoro-82M" "voices/af_heart.pt" "$VOICES_DIR/af_heart.pt"
else
    echo "Voice af_heart exists, skipping"
fi

# am_liam - US male
if [ ! -f "$VOICES_DIR/am_liam.pt" ]; then
    download_hf "hexgrad/Kokoro-82M" "voices/am_liam.pt" "$VOICES_DIR/am_liam.pt"
else
    echo "Voice am_liam exists, skipping"
fi

# --- Russian G2P support files ---------------------------------

if [ ! -f "ru_g2p.py" ]; then
    download_hf "zaakirio/kokoro-ru" "ru_g2p.py" "ru_g2p.py"
else
    echo "ru_g2p.py exists, skipping"
fi

if [ ! -d "espeak-data" ]; then
    mkdir -p espeak-data
    docker run --rm -v "$(pwd):/app" python:3.11-slim bash -c "
        pip install -q huggingface_hub && \
        python3 -c \"
from huggingface_hub import snapshot_download
import os, shutil
path = snapshot_download('zaakirio/kokoro-ru', allow_patterns='espeak-data/*')
src = os.path.join(path, 'espeak-data')
dst = '/app/espeak-data'
if os.path.exists(dst):
    shutil.rmtree(dst)
shutil.copytree(src, dst)
print('espeak-data downloaded')
\""
else
    echo "espeak-data exists, skipping"
fi

for cfg in config.json tokenizer.json tokenizer_config.json kokoro-config.json; do
    if [ ! -f "$cfg" ]; then
        download_hf "zaakirio/kokoro-ru" "$cfg" "$cfg"
    else
        echo "$cfg exists, skipping"
    fi
done

echo "Download complete!"
ls -la "$MODELS_DIR" "$VOICES_DIR"