#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEPLOY_HOST="${DEPLOY_HOST:-192.168.0.131}"
DEPLOY_USER="${DEPLOY_USER:-shampad}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${DEPLOY_USER}/projects}"
PROD_DEPLOY_PATH="${PROD_DEPLOY_PATH:-${PROJECTS_DIR}/isl-marketing-agent}"
APP_PORT="${APP_PORT:-8020}"
SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"
CONTROL_PATH="/tmp/islma-%C"
SSH_OPTS=(
    -o ConnectTimeout=15
    -o ServerAliveInterval=10
    -o ServerAliveCountMax=3
    -o ControlMaster=auto
    -o ControlPersist=300
    -o "ControlPath=${CONTROL_PATH}"
)
NO_CACHE=0
SKIP_HEALTH=0
SYNC_ENV=1

usage() {
    cat <<'EOF'
Usage: ./deploy.sh [options]

Deploy the InariSoftLabs Marketing Agent with Docker Compose.

Options:
  --host HOST       SSH host
  --user USER       SSH user
  --projects PATH   Remote projects directory
  --path PATH       Full remote application directory
  --port PORT       Published application port (default: 8020)
  --no-cache        Build the Docker image without cache
  --keep-env        Keep an existing remote .env instead of uploading the local one
  --skip-health     Skip the post-deployment health check
  -h, --help        Show this help

The SSH password is requested interactively and is never stored in this script.
EOF
}

log() {
    printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

shell_quote() {
    printf '%q' "$1"
}

remote() {
    ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"
}

close_connection() {
    ssh "${SSH_OPTS[@]}" -O exit "$SSH_TARGET" >/dev/null 2>&1 || true
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --host)
                [[ $# -ge 2 ]] || die "--host requires a value"
                DEPLOY_HOST="$2"
                shift
                ;;
            --user)
                [[ $# -ge 2 ]] || die "--user requires a value"
                DEPLOY_USER="$2"
                shift
                ;;
            --projects)
                [[ $# -ge 2 ]] || die "--projects requires a value"
                PROJECTS_DIR="$2"
                PROD_DEPLOY_PATH="${PROJECTS_DIR}/isl-marketing-agent"
                shift
                ;;
            --path)
                [[ $# -ge 2 ]] || die "--path requires a value"
                PROD_DEPLOY_PATH="$2"
                shift
                ;;
            --port)
                [[ $# -ge 2 ]] || die "--port requires a value"
                APP_PORT="$2"
                shift
                ;;
            --no-cache)
                NO_CACHE=1
                ;;
            --keep-env)
                SYNC_ENV=0
                ;;
            --skip-health)
                SKIP_HEALTH=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown option: $1"
                ;;
        esac
        shift
    done
    SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"
}

verify_remote() {
    log "Checking deployment host"
    remote "command -v docker >/dev/null && docker compose version >/dev/null"
    remote "mkdir -p $(shell_quote "$PROD_DEPLOY_PATH/data/uploads")"
}

sync_project() {
    log "Syncing project to ${SSH_TARGET}:${PROD_DEPLOY_PATH}"
    if remote "command -v rsync >/dev/null 2>&1"; then
        rsync -az --delete --stats \
            -e "ssh -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o ControlMaster=auto -o ControlPersist=300 -o ControlPath=${CONTROL_PATH}" \
            --exclude '.git' \
            --exclude '.env' \
            --exclude 'data' \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '.pytest_cache' \
            --exclude '.ruff_cache' \
            --exclude '.venv' \
            "$ROOT_DIR/" "$SSH_TARGET:$PROD_DEPLOY_PATH/"
        return
    fi

    log "Remote rsync is unavailable; using a tar archive over SSH"
    tar -czf - \
        --exclude='.git' \
        --exclude='.env' \
        --exclude='data' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.pytest_cache' \
        --exclude='.ruff_cache' \
        --exclude='.venv' \
        -C "$ROOT_DIR" . \
        | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "mkdir -p $(shell_quote "$PROD_DEPLOY_PATH") && tar -xzf - -C $(shell_quote "$PROD_DEPLOY_PATH")"
}

sync_env() {
    if [[ "$SYNC_ENV" -eq 0 ]]; then
        remote "test -s $(shell_quote "$PROD_DEPLOY_PATH/.env")" || die "Remote .env does not exist or is empty"
        return
    fi
    [[ -s "$ROOT_DIR/.env" ]] || die "Local .env is missing or empty; use --keep-env only when the server already has one"
    log "Uploading environment configuration"
    scp "${SSH_OPTS[@]}" "$ROOT_DIR/.env" "$SSH_TARGET:$(shell_quote "$PROD_DEPLOY_PATH/.env")"
    remote "chmod 600 $(shell_quote "$PROD_DEPLOY_PATH/.env")"
}

deploy() {
    local build_flag=""
    if [[ "$NO_CACHE" -eq 1 ]]; then
        build_flag="--no-cache"
    fi
    log "Building application image"
    remote "cd $(shell_quote "$PROD_DEPLOY_PATH") && APP_PORT=$(shell_quote "$APP_PORT") docker compose -f docker-compose.prod.yml build $build_flag"

    log "Restarting application"
    remote "cd $(shell_quote "$PROD_DEPLOY_PATH") && APP_PORT=$(shell_quote "$APP_PORT") docker compose -f docker-compose.prod.yml up -d --remove-orphans"
}

health_check() {
    if [[ "$SKIP_HEALTH" -eq 1 ]]; then
        return
    fi
    log "Checking application health"
    remote "for attempt in \$(seq 1 15); do
        code=\$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:$(shell_quote "$APP_PORT")/healthz 2>/dev/null || true)
        if [ \"\$code\" = '200' ]; then
            echo 'Marketing Agent healthy (HTTP 200)'
            exit 0
        fi
        echo \"Attempt \$attempt/15: HTTP \${code:-000}\"
        sleep 3
    done
    cd $(shell_quote "$PROD_DEPLOY_PATH")
    docker compose -f docker-compose.prod.yml ps
    docker compose -f docker-compose.prod.yml logs --tail 80 marketing-agent
    exit 1"
}

main() {
    parse_args "$@"
    require_cmd ssh
    require_cmd scp
    require_cmd rsync
    [[ -f "$ROOT_DIR/Dockerfile" ]] || die "Missing Dockerfile"
    [[ -f "$ROOT_DIR/docker-compose.prod.yml" ]] || die "Missing docker-compose.prod.yml"
    [[ "$APP_PORT" =~ ^[0-9]+$ ]] || die "Port must be numeric"

    trap close_connection EXIT
    log "Deploy target: ${SSH_TARGET}:${PROD_DEPLOY_PATH} (port ${APP_PORT})"
    verify_remote
    sync_project
    sync_env
    deploy
    health_check
    log "Deploy complete: http://${DEPLOY_HOST}:${APP_PORT}"
}

main "$@"
