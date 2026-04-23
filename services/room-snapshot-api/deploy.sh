#!/bin/bash

# ==============================================================================
# room-snapshot-api deployment script
# Supports two deployment modes:
#   - local: Deploy on the same server as FLAI application
#   - remote: Deploy on a separate server
# ==============================================================================

set -euo pipefail  # Exit on error, undefined vars, pipe failures

# Configuration
ENV_FILE="room-snapshot-api/.env"
ENV_EXAMPLE="room-snapshot-api/.env.example"
SERVICE_DIR="room-snapshot-api"
MAX_HEALTH_RETRIES=15
HEALTH_RETRY_DELAY=2

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ==============================================================================
# Utility functions
# ==============================================================================

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "\n${CYAN}━━━ $1 ━━━${NC}"
}

# Detect docker compose command (v2 plugin vs standalone)
detect_compose() {
    if command -v docker &>/dev/null && docker compose version &>/dev/null; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        print_error "Neither 'docker compose' nor 'docker-compose' found. Install Docker Compose."
        exit 1
    fi
    print_info "Using: ${COMPOSE_CMD}"
}

# ==============================================================================
# .env management
# ==============================================================================

generate_secret_key() {
    openssl rand -hex 32 2>/dev/null || \
    python3 -c "import secrets; print(secrets.token_hex(32))" || \
    cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 64
}

init_env() {
    local compose_file="$1"

    if [ ! -f "$ENV_FILE" ]; then
        print_warn "${ENV_FILE} not found. Creating from ${ENV_EXAMPLE}..."
        if [ -f "$ENV_EXAMPLE" ]; then
            cp "$ENV_EXAMPLE" "$ENV_FILE"
        else
            touch "$ENV_FILE"
        fi
    fi

    # Generate SECRET_KEY if missing or still default
    local has_secret=false
    if grep -q "^SECRET_KEY=" "$ENV_FILE" 2>/dev/null; then
        local current_key
        current_key=$(grep "^SECRET_KEY=" "$ENV_FILE" | sed 's/^SECRET_KEY=//' | tr -d '"' | tr -d "'")
        if [ -n "$current_key" ] && [ "$current_key" != "change-me-in-production" ] && [ "$current_key" != "dev-secret-key-change-in-production" ] && [ "$current_key" != "change-this-secret-key-in-production" ]; then
            has_secret=true
        fi
    fi

    if [ "$has_secret" = false ]; then
        local new_key
        new_key=$(generate_secret_key)
        if grep -q "^SECRET_KEY=" "$ENV_FILE" 2>/dev/null; then
            sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${new_key}|" "$ENV_FILE"
        else
            echo "SECRET_KEY=${new_key}" >> "$ENV_FILE"
        fi
        print_info "Generated new SECRET_KEY"
    fi
}

# ==============================================================================
# Health checking
# ==============================================================================

wait_for_healthy() {
    local compose_file="$1"
    local container_name
    local api_url

    case "$compose_file" in
        *local*)
            container_name="flai-room-snapshot-api"
            api_url="http://localhost:5005/health"
            ;;
        *remote*)
            container_name="room-snapshot-api"
            api_url="http://localhost:5005/health"
            ;;
        *)
            container_name=""
            api_url="http://localhost:5005/health"
            ;;
    esac

    print_info "Waiting for service to become healthy..."

    for i in $(seq 1 $MAX_HEALTH_RETRIES); do
        # Check container status
        local container_state
        container_state=$($COMPOSE_CMD -f "$compose_file" ps --format '{{.State}}' 2>/dev/null | head -1)

        if [ "$container_state" = "running" ]; then
            # Container is running, try health endpoint
            local http_code
            http_code=$(curl -s -o /dev/null -w '%{http_code}' "$api_url" 2>/dev/null || echo "000")

            if [ "$http_code" = "200" ]; then
                local health_response
                health_response=$(curl -s "$api_url" 2>/dev/null)
                print_info "Service is healthy!"
                if command -v python3 &>/dev/null; then
                    echo "$health_response" | python3 -m json.tool 2>/dev/null || echo "$health_response"
                else
                    echo "$health_response"
                fi
                return 0
            fi
            print_info "  Container running, waiting for health endpoint (attempt ${i}/${MAX_HEALTH_RETRIES}, HTTP ${http_code})..."
        else
            print_info "  Container starting... (${i}/${MAX_HEALTH_RETRIES})"
        fi

        sleep $HEALTH_RETRY_DELAY
    done

    print_error "Service did not become healthy within $((MAX_HEALTH_RETRIES * HEALTH_RETRY_DELAY)) seconds."
    print_error "Check logs:"
    print_error "  ${COMPOSE_CMD} -f ${compose_file} logs --tail=50"
    return 1
}

# ==============================================================================
# Deployment functions
# ==============================================================================

deploy_local() {
    print_step "Starting LOCAL deployment (same server as FLAI)"

    detect_compose
    init_env "docker-compose-local.yml"

    # Check/create Docker network
    if ! docker network ls --format '{{.Name}}' | grep -q "^flai_flai_network$"; then
        print_warn "Docker network 'flai_flai_network' not found. Creating..."
        docker network create flai_flai_network
        print_info "Network created."
    else
        print_info "Docker network 'flai_flai_network' exists."
    fi

    # Build
    print_step "Building Docker image"
    $COMPOSE_CMD -f docker-compose-local.yml build

    # Start
    print_step "Starting service"
    $COMPOSE_CMD -f docker-compose-local.yml up -d

    # Health check
    if wait_for_healthy "docker-compose-local.yml"; then
        print_step "✅ Deployment successful!"
        echo ""
        echo "  API:            http://localhost:5005"
        echo "  Health:         http://localhost:5005/health"
        echo "  Rooms list:     http://localhost:5005/rooms"
        echo "  Logs:           ${COMPOSE_CMD} -f docker-compose-local.yml logs -f"
        echo ""
        echo "  Next step: Configure cameras in ${SERVICE_DIR}/config/cameras.conf"
        echo "  Then restart:  ${COMPOSE_CMD} -f docker-compose-local.yml restart"
    else
        print_step "❌ Deployment may have issues"
        print_info "Recent logs:"
        $COMPOSE_CMD -f docker-compose-local.yml logs --tail=20 || true
        exit 1
    fi
}

deploy_remote() {
    print_step "Starting REMOTE deployment (separate server)"

    detect_compose
    init_env "docker-compose-remote.yml"

    # Build
    print_step "Building Docker image"
    $COMPOSE_CMD -f docker-compose-remote.yml build

    # Start
    print_step "Starting service"
    $COMPOSE_CMD -f docker-compose-remote.yml up -d

    # Health check
    if wait_for_healthy "docker-compose-remote.yml"; then
        print_step "✅ Deployment successful!"
        echo ""
        echo "  API:            http://<this-server-ip>:5005"
        echo "  Health:         http://<this-server-ip>:5005/health"
        echo "  Rooms list:     http://<this-server-ip>:5005/rooms"
        echo "  Logs:           ${COMPOSE_CMD} -f docker-compose-remote.yml logs -f"
        echo ""
        echo "  IMPORTANT:"
        echo "  1. Open firewall for FLAI server access:"
        echo "     sudo ufw allow from <flai-server-ip> to any port 5005"
        echo ""
        echo "  2. Update FLAI .env:"
        echo "     CAMERA_API_URL=http://<this-server-ip>:5005"
        echo ""
        echo "  3. Configure cameras in ${SERVICE_DIR}/config/cameras.conf"
        echo "  4. Restart: ${COMPOSE_CMD} -f docker-compose-remote.yml restart"
    else
        print_step "❌ Deployment may have issues"
        print_info "Recent logs:"
        $COMPOSE_CMD -f docker-compose-remote.yml logs --tail=20 || true
        exit 1
    fi
}

# ==============================================================================
# Usage
# ==============================================================================

show_usage() {
    echo "Usage: $0 {local|remote|status|logs|stop|restart}"
    echo ""
    echo "Commands:"
    echo "  local    Deploy on the SAME server as FLAI application"
    echo "  remote   Deploy on a SEPARATE server"
    echo "  status   Show service status"
    echo "  logs     Show recent logs"
    echo "  stop     Stop the service"
    echo "  restart  Restart the service"
    echo ""
    echo "Examples:"
    echo "  $0 local          # Deploy locally"
    echo "  $0 remote         # Deploy remotely"
    echo "  $0 status         # Check status"
    echo "  $0 logs           # View logs"
    echo ""
}

show_status() {
    detect_compose

    echo ""
    echo "━━━ Local Mode ━━━"
    if [ -f "docker-compose-local.yml" ]; then
        $COMPOSE_CMD -f docker-compose-local.yml ps 2>/dev/null || echo "  Not deployed locally"
    else
        echo "  docker-compose-local.yml not found"
    fi

    echo ""
    echo "━━━ Remote Mode ━━━"
    if [ -f "docker-compose-remote.yml" ]; then
        $COMPOSE_CMD -f docker-compose-remote.yml ps 2>/dev/null || echo "  Not deployed remotely"
    else
        echo "  docker-compose-remote.yml not found"
    fi

    echo ""
    echo "━━━ Health Check ━━━"
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:5005/health 2>/dev/null || echo "000")
    if [ "$http_code" = "200" ]; then
        curl -s http://localhost:5005/health | python3 -m json.tool 2>/dev/null || curl -s http://localhost:5005/health
    else
        echo "  Service not reachable on localhost:5005 (HTTP ${http_code})"
    fi
    echo ""
}

show_logs() {
    detect_compose
    $COMPOSE_CMD -f docker-compose-local.yml logs --tail=50 -f 2>/dev/null || \
    $COMPOSE_CMD -f docker-compose-remote.yml logs --tail=50 -f 2>/dev/null || \
    print_error "No running service found."
}

stop_service() {
    detect_compose
    print_info "Stopping service..."
    $COMPOSE_CMD -f docker-compose-local.yml down 2>/dev/null || \
    $COMPOSE_CMD -f docker-compose-remote.yml down 2>/dev/null || true
    print_info "Service stopped."
}

restart_service() {
    detect_compose
    print_info "Restarting service..."
    $COMPOSE_CMD -f docker-compose-local.yml restart 2>/dev/null || \
    $COMPOSE_CMD -f docker-compose-remote.yml restart 2>/dev/null || \
    print_error "No running service found."
}

# ==============================================================================
# Main
# ==============================================================================

main() {
    # Script must be run from the room-snapshot-api deployment directory
    # (where docker-compose-*.yml files live)
    if [ $# -eq 0 ]; then
        print_error "No command specified!"
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
        status)
            show_status
            ;;
        logs)
            show_logs
            ;;
        stop)
            stop_service
            ;;
        restart)
            restart_service
            ;;
        -h|--help|help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown command: $1"
            show_usage
            exit 1
            ;;
    esac
}

main "$@"
