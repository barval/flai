#!/bin/bash

# ==============================================================================
# room-snapshot-api deployment script
# This script clones the repository, configures environment, 
# generates SECRET_KEY, and starts the service via docker-compose
# ==============================================================================

set -e  # Exit immediately if a command exits with a non-zero status

# Configuration
REPO_URL="https://github.com/barval/room-snapshot-api.git"
REPO_DIR="room-snapshot-api"
ENV_EXAMPLE=".env.example"
ENV_FILE=".env"

# Colors for output (optional, for better readability)
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
    # Generate a 64-character random string using /dev/urandom
    openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))" || cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 64
}

# Function to update or add SECRET_KEY in .env file
update_env_secret() {
    local secret="$1"
    local env_file="$2"
    
    if grep -q "^SECRET_KEY=" "$env_file"; then
        # Replace existing SECRET_KEY
        sed -i "s|^SECRET_KEY=.*|SECRET_KEY=\"${secret}\"|" "$env_file"
        print_info "Updated SECRET_KEY in ${env_file}"
    else
        # Append SECRET_KEY if not exists
        echo "SECRET_KEY=\"${secret}\"" >> "$env_file"
        print_info "Added SECRET_KEY to ${env_file}"
    fi
}

# Main execution
main() {
    print_info "Starting deployment of room-snapshot-api..."

    # Step 1: Clone or update repository
    if [ -d "$REPO_DIR" ]; then
        print_info "Repository directory exists. Pulling latest changes..."
        cd "$REPO_DIR"
        git pull origin main
    else
        print_info "Cloning repository from ${REPO_URL}..."
        git clone "$REPO_URL" "$REPO_DIR"
        cd "$REPO_DIR"
    fi

    # Step 2: Copy .env.example to .env if .env doesn't exist
    if [ ! -f "$ENV_FILE" ]; then
        if [ -f "$ENV_EXAMPLE" ]; then
            print_info "Copying ${ENV_EXAMPLE} to ${ENV_FILE}..."
            cp "$ENV_EXAMPLE" "$ENV_FILE"
        else
            print_error "${ENV_EXAMPLE} not found! Cannot proceed."
            exit 1
        fi
    else
        print_warn "${ENV_FILE} already exists. Skipping copy."
    fi

    # Step 3: Generate and insert SECRET_KEY
    print_info "Generating secure SECRET_KEY..."
    SECRET_KEY=$(generate_secret_key)
    
    if [ -z "$SECRET_KEY" ]; then
        print_error "Failed to generate SECRET_KEY!"
        exit 1
    fi
    
    update_env_secret "$SECRET_KEY" "$ENV_FILE"
    print_info "SECRET_KEY has been set successfully."

    # Step 4: Build and start services with docker-compose
    print_info "Building Docker images..."
    docker-compose build

    print_info "Starting services in detached mode..."
    docker-compose up -d

    # Step 5: Wait and check service health
    print_info "Waiting for service to start..."
    sleep 5

    # Try to check if container is running
    if docker-compose ps | grep -q "Up"; then
        print_info "✅ Service is running!"
        print_info "API should be available at: http://localhost:5005"
        print_info "Check logs with: docker-compose logs -f"
        print_info "Health endpoint: http://localhost:5005/health"
    else
        print_error "❌ Service may not have started correctly. Check logs with: docker-compose logs"
        exit 1
    fi

    print_info "Deployment completed successfully!"
}

# Run main function
main "$@"