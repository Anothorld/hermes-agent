#!/usr/bin/env bash
# Start the KOL Ops Console locally (backend + frontend dev server).
#
# Usage:
#   ./start.sh                        # start bridge + backend + frontend (Ctrl-C stops all)
#   ./start.sh bridge                 # only kol-ops-bridge plugin (port 8080)
#   ./start.sh backend                # only console backend
#   ./start.sh frontend               # only frontend
#   ./start.sh install                # (re)install deps only, do not start
#   ./start.sh reset-password --email owner@console.app
#   ./start.sh add-user --email ops@example.com --role operator
#   ./start.sh list-users
#
# Configuration: copy .env.example to .env and edit, or export KOC_* in your shell.
# A .env file in this directory is auto-loaded.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
BRIDGE_DIR="$(cd "$ROOT/../../plugins/kol-ops-bridge" && pwd)"
VENV="$BACKEND/.venv"
ENV_FILE="$ROOT/.env"

log() { printf '\033[1;32m[koc]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[koc]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[koc]\033[0m %s\n' "$*" >&2; exit 1; }

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    log "loading $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

ensure_jwt_secret() {
  if [[ -z "${KOC_JWT_SECRET:-}" ]]; then
    if command -v python3 >/dev/null; then
      KOC_JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
    else
      KOC_JWT_SECRET="dev-secret-please-override-1234567890"
    fi
    export KOC_JWT_SECRET
    warn "KOC_JWT_SECRET not set; generated an ephemeral one for this run."
  fi
}

install_backend() {
  command -v python3 >/dev/null || die "python3 not found"
  if [[ ! -d "$VENV" ]]; then
    log "creating venv at $VENV"
    python3 -m venv "$VENV"
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  python -m pip install --upgrade pip >/dev/null
  log "installing backend requirements"
  pip install -r "$BACKEND/requirements.txt"
  deactivate
}

install_frontend() {
  command -v npm >/dev/null || die "npm not found"
  if [[ ! -d "$FRONTEND/node_modules" ]]; then
    log "installing frontend deps"
    (cd "$FRONTEND" && npm install --no-audit --no-fund)
  else
    log "frontend node_modules present (skip install; run 'install' to refresh)"
  fi
}

start_backend() {
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  : "${KOC_HOST:=0.0.0.0}"
  : "${KOC_PORT:=8765}"
  log "backend → http://$KOC_HOST:$KOC_PORT"
  (cd "$BACKEND" && exec uvicorn app.main:app --host "$KOC_HOST" --port "$KOC_PORT" --reload)
}

start_frontend() {
  log "frontend → http://localhost:5173"
  (cd "$FRONTEND" && exec npm run dev -- --host)
}

start_bridge() {
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  : "${KOC_BRIDGE_HOST:=127.0.0.1}"
  : "${KOC_BRIDGE_PORT:=8080}"
  # Plugin reads its API key from this env var when available.
  if [[ -n "${KOC_BRIDGE_KEY:-}" ]]; then
    export HERMES_KOL_OPS_BRIDGE_KEY="$KOC_BRIDGE_KEY"
  fi
  log "bridge → http://$KOC_BRIDGE_HOST:$KOC_BRIDGE_PORT/api/plugins/kol-ops-bridge"
  exec python "$BRIDGE_DIR/serve.py" --host "$KOC_BRIDGE_HOST" --port "$KOC_BRIDGE_PORT"
}

run_cli() {
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  (cd "$BACKEND" && exec python -m app.cli "$@")
}

mode="${1:-all}"

case "$mode" in
  install)
    install_backend
    install_frontend
    log "install done"
    exit 0
    ;;
  reset-password|add-user|list-users)
    load_env
    [[ -d "$VENV" ]] || install_backend
    run_cli "$@"
    ;;
  backend)
    load_env
    ensure_jwt_secret
    [[ -d "$VENV" ]] || install_backend
    start_backend
    ;;
  bridge)
    load_env
    [[ -d "$VENV" ]] || install_backend
    start_bridge
    ;;
  frontend)
    [[ -d "$FRONTEND/node_modules" ]] || install_frontend
    start_frontend
    ;;
  all)
    load_env
    ensure_jwt_secret
    [[ -d "$VENV" ]] || install_backend
    [[ -d "$FRONTEND/node_modules" ]] || install_frontend

    pids=()
    cleanup() {
      log "shutting down ($*)"
      for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done
      wait 2>/dev/null || true
    }
    trap 'cleanup EXIT' EXIT
    trap 'cleanup INT; exit 130' INT
    trap 'cleanup TERM; exit 143' TERM

    start_bridge &
    pids+=("$!")
    start_backend &
    pids+=("$!")
    start_frontend &
    pids+=("$!")
    log "bridge + backend + frontend started; Ctrl-C to stop. pids=${pids[*]}"
    wait -n "${pids[@]}"
    ;;
  *)
    die "unknown mode: $mode (use: all|bridge|backend|frontend|install|reset-password|add-user|list-users)"
    ;;
esac
