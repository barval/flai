#!/bin/bash

# ==============================================================================
# Ollama Models Downloader Script
# Starts Ollama via docker-compose and pulls models listed in models.txt
# ==============================================================================

set -e  # Exit immediately if a command exits with a non-zero status
set -o pipefail  # Return exit status of the last command that failed in a pipeline

# Configuration
COMPOSE_FILE="docker-compose.yml"
CONTAINER_NAME="ollama"
MODELS_FILE="models.txt"
HEALTH_CHECK_TIMEOUT=120  # seconds
HEALTH_CHECK_INTERVAL=5   # seconds

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# Check if required files and tools exist
check_prerequisites() {
    log_step "Checking prerequisites..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "docker-compose is not installed or not in PATH"
        exit 1
    fi
    
    if [ ! -f "$COMPOSE_FILE" ]; then
        log_error "Docker Compose file '${COMPOSE_FILE}' not found in current directory"
        exit 1
    fi
    
    if [ ! -f "$MODELS_FILE" ]; then
        log_error "Models file '${MODELS_FILE}' not found in current directory"
        exit 1
    fi
    
    log_info "All prerequisites checked successfully"
}

# Start Ollama service via docker-compose
start_ollama_service() {
    log_step "Starting Ollama service..."
    
    # Check if container is already running
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log_warn "Container '${CONTAINER_NAME}' is already running"
        return 0
    fi
    
    # Determine correct docker-compose command
    if command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        COMPOSE_CMD="docker compose"
    fi
    
    # Start the service
    log_info "Running: ${COMPOSE_CMD} -f ${COMPOSE_FILE} up -d"
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d
    
    log_info "Ollama service start command executed"
}

# Wait for Ollama container to be healthy and ready
wait_for_ollama_ready() {
    log_step "Waiting for Ollama to be ready..."
    
    local elapsed=0
    
    while [ $elapsed -lt $HEALTH_CHECK_TIMEOUT ]; do
        # Check if container is running
        if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            log_warn "Container not running yet, waiting..."
            sleep $HEALTH_CHECK_INTERVAL
            elapsed=$((elapsed + HEALTH_CHECK_INTERVAL))
            continue
        fi
        
        # Check if Ollama API responds
        if docker exec "$CONTAINER_NAME" curl -s http://localhost:11434/api/tags &> /dev/null; then
            log_info "Ollama API is responding"
            return 0
        fi
        
        log_info "Ollama not ready yet, waiting... (${elapsed}s/${HEALTH_CHECK_TIMEOUT}s)"
        sleep $HEALTH_CHECK_INTERVAL
        elapsed=$((elapsed + HEALTH_CHECK_INTERVAL))
    done
    
    log_error "Timeout waiting for Ollama to become ready"
    return 1
}

# Pull a single model via docker exec
pull_model() {
    local model_name="$1"
    
    # Skip empty lines and comments
    if [[ -z "$model_name" || "$model_name" =~ ^[[:space:]]*# ]]; then
        return 0
    fi
    
    # Trim whitespace
    model_name=$(echo "$model_name" | xargs)
    
    log_info "Pulling model: ${model_name}"
    
    # Check if model already exists
    if docker exec "$CONTAINER_NAME" ollama list 2>/dev/null | grep -qw "$model_name"; then
        log_warn "Model '${model_name}' already exists, skipping"
        return 0
    fi
    
    # Pull the model
    if docker exec "$CONTAINER_NAME" ollama pull "$model_name"; then
        log_info "✅ Successfully pulled: ${model_name}"
        return 0
    else
        log_error "❌ Failed to pull: ${model_name}"
        return 1
    fi
}

# Main function to process models file and download models
download_models() {
    log_step "Processing models from '${MODELS_FILE}'..."
    
    local total_models=0
    local success_count=0
    local fail_count=0
    local skipped_count=0
    
    # Read models file line by line
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip empty lines and comments
        if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
            continue
        fi
        
        total_models=$((total_models + 1))
        
        if pull_model "$line"; then
            # Check return was skip or success
            if docker exec "$CONTAINER_NAME" ollama list 2>/dev/null | grep -qw "$(echo "$line" | xargs)"; then
                if docker exec "$CONTAINER_NAME" ollama list 2>/dev/null | grep "$(echo "$line" | xargs)" | grep -q "just now\|seconds ago\|minutes ago"; then
                    success_count=$((success_count + 1))
                else
                    skipped_count=$((skipped_count + 1))
                fi
            fi
        else
            fail_count=$((fail_count + 1))
        fi
        
    done < "$MODELS_FILE"
    
    # Summary
    echo ""
    log_info "========== Download Summary =========="
    log_info "Total models processed: ${total_models}"
    log_info "Successfully pulled:    ${success_count}"
    log_info "Already existed:        ${skipped_count}"
    log_info "Failed:                 ${fail_count}"
    log_info "======================================"
    
    if [ $fail_count -gt 0 ]; then
        return 1
    fi
    return 0
}

# Show available models after download
show_installed_models() {
    log_step "Listing installed Ollama models..."
    echo ""
    docker exec "$CONTAINER_NAME" ollama list
    echo ""
}

# Main execution
main() {
    echo ""
    log_info "=========================================="
    log_info "  Ollama Models Downloader - Starting"
    log_info "=========================================="
    echo ""
    
    # Step 1: Check prerequisites
    check_prerequisites
    
    # Step 2: Start Ollama service
    start_ollama_service
    
    # Step 3: Wait for service to be ready
    if ! wait_for_ollama_ready; then
        log_error "Failed to start Ollama service properly"
        exit 1
    fi
    
    # Step 4: Download models
    if ! download_models; then
        log_warn "Some models failed to download (see errors above)"
    fi
    
    # Step 5: Show installed models
    show_installed_models
    
    log_info "✅ Script completed successfully!"
    log_info "Ollama API available at: http://localhost:11434"
    log_info "Use: docker exec ollama ollama run <model_name>"
}

# Handle script interruption
trap 'echo ""; log_warn "Script interrupted by user"; exit 130' INT TERM

# Run main function
main "$@"