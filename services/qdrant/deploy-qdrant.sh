#!/bin/bash

# ==============================================================================
# Qdrant Deployment Script
# Creates .env file, generates SECRET_KEY, and starts Qdrant via docker-compose
# ==============================================================================

set -e  # Exit immediately if a command exits with a non-zero status
set -o pipefail  # Return exit status of the last command that failed in a pipeline

# Configuration
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"
VOLUME_NAME="qdrant_data"
NETWORK_NAME="flai_network"
CONTAINER_NAME="qdrant"

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

# Check if required tools are installed
check_prerequisites() {
    log_step "Checking prerequisites..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 1
    fi
    
    if command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    elif docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
    else
        log_error "docker-compose is not installed or not in PATH"
        exit 1
    fi
    
    if [ ! -f "$COMPOSE_FILE" ]; then
        log_error "Docker Compose file '${COMPOSE_FILE}' not found"
        exit 1
    fi
    
    log_info "All prerequisites checked successfully"
}

# Create external volume if it doesn't exist
create_volume() {
    log_step "Checking/creating Docker volume '${VOLUME_NAME}'..."
    
    if docker volume inspect "$VOLUME_NAME" &> /dev/null; then
        log_warn "Volume '${VOLUME_NAME}' already exists"
    else
        log_info "Creating volume '${VOLUME_NAME}'..."
        docker volume create "$VOLUME_NAME"
        log_info "Volume '${VOLUME_NAME}' created successfully"
    fi
}

# Create external network if it doesn't exist
create_network() {
    log_step "Checking/creating Docker network '${NETWORK_NAME}'..."
    
    if docker network inspect "$NETWORK_NAME" &> /dev/null; then
        log_warn "Network '${NETWORK_NAME}' already exists"
    else
        log_info "Creating network '${NETWORK_NAME}'..."
        docker network create "$NETWORK_NAME"
        log_info "Network '${NETWORK_NAME}' created successfully"
    fi
}

# Generate a secure random API key
generate_api_key() {
    # Try multiple methods to generate a secure random key
    if command -v openssl &> /dev/null; then
        openssl rand -hex 32
    elif command -v python3 &> /dev/null; then
        python3 -c "import secrets; print(secrets.token_hex(32))"
    else
        # Fallback to /dev/urandom
        cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 64
    fi
}

# Create .env file with API key
create_env_file() {
    log_step "Creating '${ENV_FILE}' file..."
    
    local api_key=$(generate_api_key)
    
    if [ -z "$api_key" ]; then
        log_error "Failed to generate API key"
        exit 1
    fi
    
    # Create or overwrite .env file
    cat > "$ENV_FILE" << EOF
# Qdrant API Configuration
# Generated on $(date '+%Y-%m-%d %H:%M:%S')

QDRANT_API_KEY="${api_key}"
EOF
    
    log_info "API key generated and saved to '${ENV_FILE}'"
    log_info "API Key: ${api_key}"  # Display for user to copy
}

# Start Qdrant service
start_service() {
    log_step "Starting Qdrant service..."
    
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d
    
    log_info "Docker Compose start command executed"
}

# Wait for service to be healthy
wait_for_service() {
    log_step "Waiting for Qdrant to be ready..."
    
    local timeout=60
    local interval=5
    local elapsed=0
    
    while [ $elapsed -lt $timeout ]; do
        # Check if container is running
        if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            # Check if HTTP API responds
            if curl -s http://localhost:6333/ &> /dev/null; then
                log_info "Qdrant HTTP API is responding"
                return 0
            fi
        fi
        
        log_info "Qdrant not ready yet, waiting... (${elapsed}s/${timeout}s)"
        sleep $interval
        elapsed=$((elapsed + interval))
    done
    
    log_error "Timeout waiting for Qdrant to become ready"
    return 1
}

# Show service status
show_status() {
    log_step "Qdrant Service Status:"
    echo ""
    docker ps --filter "name=${CONTAINER_NAME}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    echo ""
}

# Show connection info
show_connection_info() {
    log_info "========== Connection Information =========="
    log_info "HTTP API:  http://localhost:6333"
    log_info "gRPC API:  localhost:6334"
    log_info "API Key:   Check '${ENV_FILE}' file"
    log_info ""
    log_info "Test connection:"
    log_info "  curl -H \"api-key: <your-key>\" http://localhost:6333/"
    log_info ""
    log_info "Python client example:"
    log_info "  from qdrant_client import QdrantClient"
    log_info "  client = QdrantClient(url='http://localhost:6333', api_key='<your-key>')"
    log_info "============================================"
}

# Main execution
main() {
    echo ""
    log_info "=========================================="
    log_info "  Qdrant Deployment Script - Starting"
    log_info "=========================================="
    echo ""
    
    # Step 1: Check prerequisites
    check_prerequisites
    
    # Step 2: Create external volume
    create_volume
    
    # Step 3: Create external network
    create_network
    
    # Step 4: Create .env file with API key
    create_env_file
    
    # Step 5: Start service
    start_service
    
    # Step 6: Wait for service to be ready
    if ! wait_for_service; then
        log_error "Failed to start Qdrant service properly"
        log_warn "Check logs with: docker-compose logs qdrant"
        exit 1
    fi
    
    # Step 7: Show status and connection info
    show_status
    show_connection_info
    
    log_info "✅ Deployment completed successfully!"
}

# Handle script interruption
trap 'echo ""; log_warn "Script interrupted by user"; exit 130' INT TERM

# Run main function
main "$@"