#!/usr/bin/env bash
# Start the KOL Ops Console locally (backend + frontend dev server).
#
# Usage:
#   ./start.sh                        # start bridge + backend + frontend (Ctrl-C stops all)
#   ./start.sh restart                # full restart: SIGTERM → SIGKILL any old bridge/backend/frontend (by port AND command pattern), wait for ports to free, then start all
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

if [ -z "${BASH_VERSION:-}" ]; then
  script_path="$0"
  if [ -n "${ZSH_VERSION:-}" ]; then
    script_path="$(eval 'printf "%s" "${(%):-%x}"')"
  fi
  if [ ! -f "$script_path" ]; then
    printf '\033[1;31m[koc]\033[0m %s\n' "bash is required; run this script as: bash path/to/start.sh" >&2
    return 2 2>/dev/null || exit 2
  fi
  bash_bin="$(command -v bash 2>/dev/null || true)"
  if [ -z "$bash_bin" ]; then
    printf '\033[1;31m[koc]\033[0m %s\n' "bash not found" >&2
    return 2 2>/dev/null || exit 2
  fi
  "$bash_bin" "$script_path" "$@"
  exit_code=$?
  return "$exit_code" 2>/dev/null || exit "$exit_code"
fi

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  bash "${BASH_SOURCE[0]}" "$@"
  return $?
fi

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
BRIDGE_DIR="$(cd "$ROOT/../../plugins/kol-ops-bridge" && pwd)"
VENV="$BACKEND/.venv"
VENV_PYTHON="$VENV/bin/python3"
ENV_FILE="$ROOT/.env"

log() { printf '\033[1;32m[koc]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[koc]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[koc]\033[0m %s\n' "$*" >&2; exit 1; }

is_placeholder_bridge_key() {
  local value="${1:-}"
  [[ -z "$value" || "$value" == "replace-with-bridge-key" || "$value" == "change-me" ]]
}

normalize_bridge_key_env() {
  if ! is_placeholder_bridge_key "${KOC_BRIDGE_KEY:-}"; then
    if is_placeholder_bridge_key "${HERMES_KOL_OPS_BRIDGE_KEY:-}"; then
      export HERMES_KOL_OPS_BRIDGE_KEY="$KOC_BRIDGE_KEY"
    fi
  elif ! is_placeholder_bridge_key "${HERMES_KOL_OPS_BRIDGE_KEY:-}"; then
    if is_placeholder_bridge_key "${KOC_BRIDGE_KEY:-}"; then
      export KOC_BRIDGE_KEY="$HERMES_KOL_OPS_BRIDGE_KEY"
    fi
  fi
}

prepend_path_if_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  case ":$PATH:" in
    *":$dir:"*) ;;
    *) export PATH="$dir:$PATH" ;;
  esac
}

init_dev_path() {
  prepend_path_if_dir "/opt/homebrew/bin"
  prepend_path_if_dir "/usr/local/bin"
  prepend_path_if_dir "$HOME/.volta/bin"
  prepend_path_if_dir "$HOME/.asdf/shims"
  prepend_path_if_dir "$HOME/.local/share/mise/shims"

  if ! command -v npm >/dev/null; then
    local nvm_home="${NVM_DIR:-$HOME/.nvm}"
    if [[ -s "$nvm_home/nvm.sh" ]]; then
      export NVM_DIR="$nvm_home"
      # shellcheck disable=SC1090
      source "$NVM_DIR/nvm.sh"
    fi
  fi
}

backend_venv_usable() {
  [[ -x "$VENV_PYTHON" ]] || return 1
  "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1
}

backend_deps_installed() {
  backend_venv_usable && "$VENV_PYTHON" -c 'import google_auth_httplib2, google_auth_oauthlib, googleapiclient, uvicorn' >/dev/null 2>&1
}

ensure_backend_installed() {
  backend_deps_installed || install_backend
}

require_backend_python() {
  backend_venv_usable || die "backend venv is missing or unusable; run ./start.sh install"
}

require_npm() {
  init_dev_path
  command -v npm >/dev/null || die "npm not found; install Node.js/npm or configure nvm, then rerun ./start.sh install"
  command -v node >/dev/null || die "node not found; install Node.js or configure nvm, then rerun ./start.sh install"
}

frontend_deps_installed() {
  [[ -d "$FRONTEND/node_modules" ]] || return 1
  (cd "$FRONTEND" && node -e "require('rollup')" >/dev/null 2>&1)
}

ensure_frontend_installed() {
  frontend_deps_installed || install_frontend
}

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    log "loading $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
  normalize_bridge_key_env
  init_dev_path
  resolve_hermes_home
}

resolve_hermes_home() {
  # When HERMES_HOME is unset and ~/.hermes/active_profile names a
  # non-default profile, point HERMES_HOME at that profile dir so the
  # bridge — and any subprocess it spawns (Gmail CLI, dispatcher,
  # reply watcher) — reads the right google_token.json / poller_state.
  # Without this, bridge subprocesses warn "[HERMES_HOME fallback]" and
  # load the default profile's Gmail token, causing thread/draft lookups
  # to silently target the wrong mailbox.
  if [[ -n "${HERMES_HOME:-}" ]]; then
    return
  fi
  local active_path="$HOME/.hermes/active_profile"
  [[ -f "$active_path" ]] || return
  local profile
  profile="$(tr -d '\n\r \t' < "$active_path" 2>/dev/null || true)"
  if [[ -z "$profile" || "$profile" == "default" ]]; then
    return
  fi
  local profile_dir="$HOME/.hermes/profiles/$profile"
  if [[ ! -d "$profile_dir" ]]; then
    warn "active_profile=$profile but $profile_dir is missing; leaving HERMES_HOME unset"
    return
  fi
  export HERMES_HOME="$profile_dir"
  log "HERMES_HOME → $profile_dir (active_profile=$profile)"
}

ensure_jwt_secret() {
  init_dev_path
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
  init_dev_path
  command -v python3 >/dev/null || die "python3 not found"
  if [[ -d "$VENV" ]] && ! backend_venv_usable; then
    warn "backend venv is not usable on this machine; recreating it"
    python3 -m venv --clear "$VENV"
  elif [[ ! -d "$VENV" ]]; then
    log "creating venv at $VENV"
    python3 -m venv "$VENV"
  fi
  require_backend_python
  "$VENV_PYTHON" -m pip install --upgrade pip >/dev/null
  log "installing backend requirements"
  "$VENV_PYTHON" -m pip install -r "$BACKEND/requirements.txt"
}

install_frontend() {
  require_npm
  if frontend_deps_installed; then
    log "frontend node_modules present and usable (skip install)"
  else
    if [[ -d "$FRONTEND/node_modules" ]]; then
      warn "frontend node_modules are not usable on this machine; running npm install"
    fi
    log "installing frontend deps"
    (cd "$FRONTEND" && npm install --no-audit --no-fund)
  fi
}

start_backend() {
  require_backend_python
  : "${KOC_HOST:=0.0.0.0}"
  : "${KOC_PORT:=8765}"
  log "backend → http://$KOC_HOST:$KOC_PORT"
  (cd "$BACKEND" && exec "$VENV_PYTHON" -m uvicorn app.main:app --host "$KOC_HOST" --port "$KOC_PORT" --reload --reload-dir app)
}

start_frontend() {
  require_npm
  : "${KOC_FRONTEND_PORT:=5173}"
  log "frontend → http://localhost:$KOC_FRONTEND_PORT"
  (cd "$FRONTEND" && exec npm run dev -- --host --port "$KOC_FRONTEND_PORT")
}

start_bridge() {
  require_backend_python
  : "${KOC_BRIDGE_HOST:=127.0.0.1}"
  : "${KOC_BRIDGE_PORT:=8080}"
  log "bridge → http://$KOC_BRIDGE_HOST:$KOC_BRIDGE_PORT/api/plugins/kol-ops-bridge"
  local bridge_log_dir="$HOME/.hermes/kol-ops-bridge"
  local bridge_log="$bridge_log_dir/bridge.log"
  mkdir -p "$bridge_log_dir"
  log "bridge log → $bridge_log (tail -f to follow)"
  exec "$VENV_PYTHON" "$BRIDGE_DIR/serve.py" --host "$KOC_BRIDGE_HOST" --port "$KOC_BRIDGE_PORT" >>"$bridge_log" 2>&1
}

run_cli() {
  require_backend_python
  (cd "$BACKEND" && exec "$VENV_PYTHON" -m app.cli "$@")
}

list_port_pids() {
  local port="$1"
  if command -v lsof >/dev/null; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  elif command -v fuser >/dev/null; then
    fuser -n tcp "$port" 2>/dev/null | tr ' ' '\n' || true
  else
    warn "cannot inspect port $port; install lsof or psmisc/fuser for restart cleanup"
  fi
}

list_pids_by_pattern() {
  # Match against full command line (-f). Exclude this script's own PID so we
  # don't try to kill ourselves when the pattern coincidentally matches.
  local pattern="$1"
  pgrep -f "$pattern" 2>/dev/null | awk -v self="$$" '$1!=self && NF' || true
}

# Send SIGTERM, wait up to ~5s for graceful exit, then SIGKILL any survivors.
# Required so `restart` always reaches a clean slate even when a process is
# stuck in startup (e.g. bridge's gmail_poller blocking on a network call) or
# refuses to honor SIGTERM in time for the new processes to bind their ports.
kill_pids_gracefully() {
  local label="$1"; shift
  local pids=("$@")
  [[ ${#pids[@]} -eq 0 ]] && return 0
  warn "stopping $label: ${pids[*]}"
  kill "${pids[@]}" 2>/dev/null || true
  local waited=0
  while (( waited < 50 )); do
    local alive=()
    local pid
    for pid in "${pids[@]}"; do
      kill -0 "$pid" 2>/dev/null && alive+=("$pid")
    done
    [[ ${#alive[@]} -eq 0 ]] && return 0
    sleep 0.1
    waited=$((waited + 1))
  done
  local survivors=()
  local pid
  for pid in "${pids[@]}"; do
    kill -0 "$pid" 2>/dev/null && survivors+=("$pid")
  done
  if (( ${#survivors[@]} > 0 )); then
    warn "force-killing $label (SIGKILL): ${survivors[*]}"
    kill -9 "${survivors[@]}" 2>/dev/null || true
  fi
}

stop_port_listeners() {
  local label="$1"
  local port="$2"
  local pids=()
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && pids+=("$pid")
  done < <(list_port_pids "$port" | awk 'NF' | sort -u)
  if [[ ${#pids[@]} -eq 0 ]]; then
    log "no existing $label listener on port $port"
    return
  fi
  kill_pids_gracefully "$label (port $port)" "${pids[@]}"
}

stop_processes_by_pattern() {
  local label="$1"
  local pattern="$2"
  local pids=()
  while IFS= read -r pid; do
    [[ -n "$pid" ]] && pids+=("$pid")
  done < <(list_pids_by_pattern "$pattern" | sort -u)
  if [[ ${#pids[@]} -eq 0 ]]; then
    return
  fi
  kill_pids_gracefully "$label" "${pids[@]}"
}

# Block until nothing is listening on $port, or the budget expires. Used after
# the kill loop to make sure `start_all` doesn't race the OS releasing the port.
wait_port_free() {
  local label="$1"
  local port="$2"
  local waited=0
  while (( waited < 100 )); do
    if [[ -z "$(list_port_pids "$port" | awk 'NF')" ]]; then
      return 0
    fi
    sleep 0.1
    waited=$((waited + 1))
  done
  warn "$label port $port still occupied after 10s; new process may fail to bind"
  return 1
}

wait_for_any_service() {
  local running pid exit_code
  # macOS ships Bash 3.2, which does not support `wait -n`.
  while :; do
    running="$(jobs -pr || true)"
    for pid in "${pids[@]:-}"; do
      if ! printf '%s\n' "$running" | grep -qx "$pid"; then
        if wait "$pid"; then
          exit_code=0
        else
          exit_code=$?
        fi
        return "$exit_code"
      fi
    done
    sleep 1
  done
}

restart_existing() {
  load_env
  : "${KOC_PORT:=8765}"
  : "${KOC_BRIDGE_PORT:=8080}"
  : "${KOC_FRONTEND_PORT:=5173}"
  log "fully restarting dev servers — stopping any existing processes"

  # Pass 1: port listeners. Catches every process actually bound to our ports,
  # regardless of how it was launched (start.sh, manual uvicorn, ad-hoc serve).
  stop_port_listeners "bridge" "$KOC_BRIDGE_PORT"
  stop_port_listeners "backend" "$KOC_PORT"
  stop_port_listeners "frontend" "$KOC_FRONTEND_PORT"

  # Pass 2: process-pattern sweep. Catches anything our previous launches left
  # behind that wasn't listening on the expected port: hung serve.py during
  # startup before bind(), `npm run dev` wrappers, vite's esbuild helper, etc.
  stop_processes_by_pattern "kol-ops-bridge serve.py" \
    "plugins/kol-ops-bridge/serve\.py"
  stop_processes_by_pattern "kol-ops-console backend (uvicorn)" \
    "uvicorn .*app\.main:app"
  stop_processes_by_pattern "kol-ops-console frontend (vite)" \
    "kol-ops-console/frontend.*vite"
  stop_processes_by_pattern "kol-ops-console frontend (esbuild)" \
    "kol-ops-console/frontend.*esbuild"
  stop_processes_by_pattern "kol-ops-console frontend (npm run dev)" \
    "kol-ops-console/frontend.*npm.*run dev"

  # Final guard: don't return until the ports are demonstrably free, otherwise
  # the upcoming start_all will hit EADDRINUSE.
  wait_port_free "bridge" "$KOC_BRIDGE_PORT" || true
  wait_port_free "backend" "$KOC_PORT" || true
  wait_port_free "frontend" "$KOC_FRONTEND_PORT" || true

  log "restart cleanup complete; starting fresh"
  ensure_jwt_secret
  ensure_backend_installed
  ensure_frontend_installed
}

start_all() {
  load_env
  ensure_jwt_secret
  ensure_backend_installed
  ensure_frontend_installed

  pids=()
  cleanup() {
    log "shutting down ($*)"
    # Mirror restart_existing: SIGTERM, brief wait, then SIGKILL stragglers so
    # Ctrl-C never leaves the user with zombies that the next `restart` has to
    # clean up.
    local live=()
    local p
    for p in "${pids[@]:-}"; do
      [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null && live+=("$p")
    done
    if (( ${#live[@]} > 0 )); then
      kill_pids_gracefully "dev servers" "${live[@]}"
    fi
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
  wait_for_any_service
}

mode="${1:-all}"

case "$mode" in
  restart)
    restart_existing
    start_all
    ;;
  install)
    install_backend
    install_frontend
    log "install done"
    exit 0
    ;;
  reset-password|add-user|list-users)
    load_env
    ensure_backend_installed
    run_cli "$@"
    ;;
  backend)
    load_env
    ensure_jwt_secret
    ensure_backend_installed
    start_backend
    ;;
  bridge)
    load_env
    ensure_backend_installed
    start_bridge
    ;;
  frontend)
    ensure_frontend_installed
    start_frontend
    ;;
  all)
    start_all
    ;;
  *)
    die "unknown mode: $mode (use: all|restart|bridge|backend|frontend|install|reset-password|add-user|list-users)"
    ;;
esac
