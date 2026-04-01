#!/bin/bash

# ==============================================================================
# room-snapshot-api deployment script
# Supports two deployment modes:
#   - local: Deploy on the same server as FLAI application
#   - remote: Deploy on a separate server
# ==============================================================================

set -e  # Exit immediately if a command exits with a non-zero status

# Configuration
REPO_DIR="room-snapshot-api"
ENV_EXAMPLE=".env.example"
ENV_FILE=".env"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to generate a secure random SECRET_KEY
generate_secret_key() {
    openssl rand -hex 32 2>/dev/null || \
    python3 -c "import secrets; print(secrets.token_hex(32))" || \
    cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 64
}

# Function to update or add SECRET_KEY in .env file
update_env_secret() {
    local secret="$1"
    local env_file="$2"

    if grep -q "^SECRET_KEY=" "$env_file"; then
        sed -i "s|^SECRET_KEY=.*|SECRET_KEY=\"${secret}\"|" "$env_file"
        print_info "Updated SECRET_KEY in ${env_file}"
    else
        echo "SECRET_KEY=\"${secret}\"" >> "$env_file"
        print_info "Added SECRET_KEY to ${env_file}"
    fi
}

# Function to deploy locally (same server as FLAI)
deploy_local() {
    print_info "Starting LOCAL deployment (same server as FLAI)..."
    
    # Check if flai_flai_network exists
    if ! docker network ls | grep -q "flai_flai_network"; then
        print_warn "flai_flai_network not found. Creating network..."
        docker network create flai_flai_network
    fi
    
    # Build and start services
    print_info "Building Docker images..."
    docker-compose -f docker-compose-local.yml build
    
    print_info "Starting services in detached mode..."
    docker-compose -f docker-compose-local.yml up -d
    
    # Wait and check service health
    print_info "Waiting for service to start..."
    sleep 5
    
    if docker-compose -f docker-compose-local.yml ps | grep -q "Up"; then
        print_info "✅ Service is running!"
        print_info "API available at: http://localhost:5005"
        print_info "Health endpoint: http://localhost:5005/health"
        print_info "Check logs with: docker-compose -f docker-compose-local.yml logs -f"
    else
        print_error "❌ Service may not have started correctly. Check logs."
        exit 1
    fi
}

# Function to deploy remotely (separate server)
deploy_remote() {
    print_info "Starting REMOTE deployment (separate server)..."
    
    # Build and start services
    print_info "Building Docker images..."
    docker-compose -f docker-compose-remote.yml build
    
    print_info "Starting services in detached mode..."
    docker-compose -f docker-compose-remote.yml up -d
    
    # Wait and check service health
    print_info "Waiting for service to start..."
    sleep 5
    
    if docker-compose -f docker-compose-remote.yml ps | grep -q "Up"; then
        print_info "✅ Service is running!"
        print_info "API available at: http://<server-ip>:5005"
        print_info "Health endpoint: http://<server-ip>:5005/health"
        print_info ""
        print_info "IMPORTANT: Configure firewall to allow access from FLAI server:"
        print_info "  sudo ufw allow from <flai-server-ip> to any port 5005"
        print_info ""
        print_info "Update FLAI .env with:"
        print_info "  CAMERA_API_URL=http://<this-server-ip>:5005"
        print_info ""
        print_info "Check logs with: docker-compose -f docker-compose-remote.yml logs -f"
    else
        print_error "❌ Service may not have started correctly. Check logs."
        exit 1
    fi
}

# Show usage information
show_usage() {
    echo "Usage: $0 {local|remote}"
    echo ""
    echo "Deployment options:"
    echo "  local   - Deploy on the SAME server as FLAI application"
    echo "  remote  - Deploy on a SEPARATE server"
    echo ""
    echo "Examples:"
    echo "  $0 local   # Deploy locally"
    echo "  $0 remote  # Deploy remotely"
    echo ""
}

# Main execution
main() {
    if [ $# -eq 0 ]; then
        print_error "No deployment mode specified!"
        show_usage
        exit 1
    fi
    
    case "$1" in
        local)
            deploy_local
            ;;
        remote)
            deploy_remote
            ;;
        -h|--help|help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown deployment mode: $1"
            show_usage
            exit 1
            ;;
    esac
    
    print_info "Deployment completed successfully!"
}

# Run main function
main "$@"
