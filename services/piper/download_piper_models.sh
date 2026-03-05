#!/bin/bash
#
# download_piper_models.sh
# Pre-download Piper TTS voice models from HuggingFace
# Supports both model identifiers and full HuggingFace URLs
# Output and comments are in English
#

set -e  # Exit on error

# Configuration
MODEL_DIR="${1:-./piper_models}"  # Default to ./piper_models, or use first argument
VOICES_FILE="${2:-voices.txt}"    # Default to voices.txt, or use second argument
HF_REPO="rhasspy/piper-voices"
HF_BASE_URL="https://huggingface.co/$HF_REPO/raw/main"

# Colors for output (optional, degrades gracefully)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Convert HuggingFace blob URL to raw URL for downloading
# Input:  https://huggingface.co/.../blob/main/.../file.onnx
# Output: https://huggingface.co/.../raw/main/.../file.onnx
convert_to_raw_url() {
    local url="$1"
    echo "${url/\/blob\//\/raw\/}"
}

# Download a single file with retry logic
download_file() {
    local url="$1"
    local output_path="$2"
    local max_retries=3
    local retry_count=0

    while [ $retry_count -lt $max_retries ]; do
        if curl -fL -o "$output_path.tmp" "$url" 2>/dev/null; then
            mv "$output_path.tmp" "$output_path"
            return 0
        else
            retry_count=$((retry_count + 1))
            log_warn "Download failed (attempt $retry_count/$max_retries): $url"
            sleep 2
        fi
    done

    rm -f "$output_path.tmp"
    log_error "Failed to download after $max_retries attempts: $url"
    return 1
}

# Parse model identifier to correct HuggingFace path
# Input:  ru_RU-ruslan-medium
# Output: ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium
resolve_model_path() {
    local model_id="$1"
    
    # Format: {lang_code}-{speaker}-{quality}
    # Example: ru_RU-ruslan-medium
    
    # Extract lang_code (first part before first dash)
    local lang_code="${model_id%%-*}"
    
    # Extract region from lang_code (e.g., "ru" from "ru_RU")
    local region="${lang_code%%_*}"
    
    # Remove lang_code prefix to get "speaker-quality"
    local rest="${model_id#${lang_code}-}"
    
    # Extract speaker (everything before last dash) and quality (after last dash)
    local speaker="${rest%-*}"
    local quality="${rest##*-}"
    
    # Build and return the path
    echo "${region}/${lang_code}/${speaker}/${quality}/${model_id}"
}

# Download model pair (.onnx + .onnx.json) by identifier
download_model_by_id() {
    local model_id="$1"
    local model_path=$(resolve_model_path "$model_id")
    local base_url="${HF_BASE_URL}/${model_path}"

    local onnx_file="${model_id}.onnx"
    local json_file="${model_id}.onnx.json"
    local onnx_url="${base_url}.onnx"
    local json_url="${base_url}.onnx.json"

    local onnx_dest="${MODEL_DIR}/${onnx_file}"
    local json_dest="${MODEL_DIR}/${json_file}"

    # Download .onnx if not exists
    if [ -f "$onnx_dest" ]; then
        log_info "Skipping (exists): $onnx_file"
    else
        log_info "Downloading: $onnx_file"
        download_file "$onnx_url" "$onnx_dest" || return 1
    fi

    # Download .onnx.json if not exists
    if [ -f "$json_dest" ]; then
        log_info "Skipping (exists): $json_file"
    else
        log_info "Downloading: $json_file"
        download_file "$json_url" "$json_dest" || return 1
    fi
}

# Download model by full URL (auto-detect blob/raw)
download_model_by_url() {
    local url="$1"
    local raw_url=$(convert_to_raw_url "$url")
    local filename=$(basename "$raw_url")
    local dest="${MODEL_DIR}/${filename}"

    if [ -f "$dest" ]; then
        log_info "Skipping (exists): $filename"
        return 0
    fi

    log_info "Downloading from URL: $filename"
    download_file "$raw_url" "$dest" || return 1
}

# Process a single entry from voices list
process_entry() {
    local entry="$1"
    # Skip empty lines and comments
    [[ -z "$entry" || "$entry" =~ ^[[:space:]]*# ]] && return 0

    entry=$(echo "$entry" | xargs)  # Trim whitespace
    # Skip if empty after trimming
    [[ -z "$entry" ]] && return 0

    if [[ "$entry" =~ ^https?:// ]]; then
        # Full URL provided
        download_model_by_url "$entry"
    else
        # Model identifier (e.g., ru_RU-ruslan-medium)
        download_model_by_id "$entry"
    fi
}

# Main execution
main() {
    log_info "Piper TTS Model Downloader"
    log_info "Target directory: $MODEL_DIR"
    log_info "Voices list file: $VOICES_FILE"

    # Create model directory
    mkdir -p "$MODEL_DIR"

    # Check if voices file exists
    if [ ! -f "$VOICES_FILE" ]; then
        log_error "Voices file not found: $VOICES_FILE"
        echo "Usage: $0 [MODEL_DIR] [VOICES_FILE]"
        echo "Example voices.txt format:"
        echo "  ru_RU-ruslan-medium"
        echo "  en_US-bryce-medium"
        echo "  https://huggingface.co/rhasspy/piper-voices/raw/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx"
        exit 1
    fi

    local success_count=0
    local fail_count=0
    local total_count=0

    # Read and process each line
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip empty lines and comments (don't count them)
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        
        local trimmed=$(echo "$line" | xargs)
        [[ -z "$trimmed" ]] && continue
        
        total_count=$((total_count + 1))
        if process_entry "$trimmed"; then
            success_count=$((success_count + 1))
        else
            fail_count=$((fail_count + 1))
        fi
    done < "$VOICES_FILE"

    # Summary
    echo ""
    log_info "Download complete: $success_count/$total_count succeeded, $fail_count failed"

    if [ $fail_count -gt 0 ]; then
        exit 1
    fi
}

main "$@"