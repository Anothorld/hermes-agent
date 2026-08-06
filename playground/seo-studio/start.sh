#!/usr/bin/env bash
# Start POVISON SEO stack: Studio Bridge + povison-seo Hermes Gateway.
#
# Usage:
#   ./start.sh                 # bridge + gateway (Ctrl-C stops all)
#   ./start.sh restart         # stop then start
#   ./start.sh stop            # stop bridge + gateway
#   ./start.sh bridge          # Studio Bridge only (8766)
#   ./start.sh gateway         # hermes -p povison-seo gateway (8644)
#   ./start.sh install         # create venv + pip install
#   ./start.sh status          # show listeners
#   ./start.sh open            # print Studio URL
#
# Env (auto-loaded):
#   playground/seo-studio/.env
#   ~/.hermes/profiles/povison-seo/.env
#
# Agent API always uses profile: povison-seo (override with SEO_PROFILE).

if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
PYTHON="$VENV/bin/python3"
ENV_FILE="$ROOT/.env"
PROFILE="${SEO_PROFILE:-povison-seo}"
# Never derive from ambient HERMES_HOME (may point at another profile).
PROFILE_DIR="${SEO_PROFILE_DIR:-$HOME/.hermes/profiles/$PROFILE}"
PROFILE_ENV="$PROFILE_DIR/.env"
LOG_DIR="${SEO_LOG_DIR:-$PROFILE_DIR/logs}"

resolve_hermes_agent() {
  if [[ -n "${SEO_HERMES_AGENT:-}" && -d "$SEO_HERMES_AGENT" ]]; then
    printf '%s' "$SEO_HERMES_AGENT"
    return
  fi
  local candidate
  for candidate in "$ROOT/../../hermes-agent" "$ROOT/../hermes-agent" "$HOME/agent_prj/hermes-agent"; do
    if [[ -d "$candidate/hermes_cli" ]] || [[ -f "$candidate/pyproject.toml" ]]; then
      printf '%s' "$(cd "$candidate" && pwd)"
      return
    fi
  done
  printf '%s' "$ROOT/../../hermes-agent"
}

HERMES_AGENT="$(resolve_hermes_agent)"

log() { printf '\033[1;32m[seo]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[seo]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[seo]\033[0m %s\n' "$*" >&2; exit 1; }

load_env() {
  # Preserve systemd / caller-exported bind host (prod uses 0.0.0.0). Sourcing
  # .env files must not silently force 127.0.0.1 and hide the Studio from LAN.
  local _preset_bridge_host="${SEO_BRIDGE_HOST:-}"
  local _preset_bridge_port="${SEO_BRIDGE_PORT:-}"
  local _preset_gateway_port="${SEO_GATEWAY_PORT:-}"

  if [[ -f "$PROFILE_ENV" ]]; then
    log "loading profile env: $PROFILE_ENV"
    set -a
    # shellcheck disable=SC1090
    source "$PROFILE_ENV"
    set +a
  else
    warn "profile .env missing: $PROFILE_ENV"
  fi
  if [[ -f "$ENV_FILE" ]]; then
    log "loading $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi

  [[ -n "$_preset_bridge_host" ]] && SEO_BRIDGE_HOST="$_preset_bridge_host"
  [[ -n "$_preset_bridge_port" ]] && SEO_BRIDGE_PORT="$_preset_bridge_port"
  [[ -n "$_preset_gateway_port" ]] && SEO_GATEWAY_PORT="$_preset_gateway_port"

  : "${SEO_BRIDGE_HOST:=127.0.0.1}"
  : "${SEO_BRIDGE_PORT:=8766}"
  : "${SEO_GATEWAY_PORT:=8644}"
  : "${API_SERVER_ENABLED:=true}"
  : "${API_SERVER_PORT:=$SEO_GATEWAY_PORT}"
  # LLM for discover / brainstorm / section scripts (playground/seo-studio/.env)
  : "${SEO_LLM_BASE_URL:=https://ai-endpoint.povison-inc.com/v1}"
  : "${SEO_LLM_MODEL:=glm-5.2}"
  # Prefer playground copy (kept in repo); fall back to ~/povison-seo-studio.html
  if [[ -z "${SEO_STUDIO_HTML:-}" ]]; then
    if [[ -f "$ROOT/ui/index.html" ]]; then
      SEO_STUDIO_HTML="$ROOT/ui/index.html"
    else
      SEO_STUDIO_HTML="$HOME/povison-seo-studio.html"
    fi
  fi

  # Prefer skill with a complete scripts/ dir; fall back to default Hermes skills.
  # (Profile copies may be stale and lack the MVP scripts.)
  if [[ -z "${SEO_SKILL_DIR:-}" ]]; then
    local profile_skill="$PROFILE_DIR/skills/productivity/povison-seo-blog"
    local default_skill="$HOME/.hermes/skills/productivity/povison-seo-blog"
    if [[ -d "$profile_skill/scripts" && -f "$profile_skill/scripts/section-generate.py" ]]; then
      SEO_SKILL_DIR="$profile_skill"
    else
      SEO_SKILL_DIR="$default_skill"
    fi
  fi
  : "${SEO_RUNS_DIR:=$SEO_SKILL_DIR/runs}"

  # Bridge → Gateway
  : "${HERMES_GATEWAY_BASE:=http://127.0.0.1:$SEO_GATEWAY_PORT}"
  # Prefer API_SERVER_KEY from profile; allow HERMES_GATEWAY_KEY alias.
  if [[ -z "${HERMES_GATEWAY_KEY:-}" && -n "${API_SERVER_KEY:-}" ]]; then
    HERMES_GATEWAY_KEY="$API_SERVER_KEY"
  fi

  # Studio root (stock image module + Bridge scripts live here)
  : "${SEO_STUDIO_DIR:=$ROOT}"

  export SEO_PROFILE="$PROFILE"
  export SEO_PROFILE_DIR="$PROFILE_DIR"
  export SEO_BRIDGE_HOST SEO_BRIDGE_PORT SEO_GATEWAY_PORT
  export SEO_SKILL_DIR SEO_RUNS_DIR SEO_STUDIO_HTML SEO_STUDIO_DIR
  export HERMES_GATEWAY_BASE HERMES_GATEWAY_KEY
  export API_SERVER_ENABLED API_SERVER_PORT
  export SEO_LLM_BASE_URL SEO_LLM_API_KEY SEO_LLM_MODEL
  export HERMES_HOME="$PROFILE_DIR"
  # Pass stock image API keys through if present in .env (never hardcode).
  export UNSPLASH_ACCESS_KEY="${UNSPLASH_ACCESS_KEY:-}"
  export PEXELS_API_KEY="${PEXELS_API_KEY:-}"
}

venv_usable() {
  [[ -x "$PYTHON" ]] || return 1
  "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1
}

deps_installed() {
  venv_usable && "$PYTHON" -c 'import fastapi, uvicorn, pydantic' >/dev/null 2>&1
}

install_deps() {
  command -v python3 >/dev/null || die "python3 not found"
  if [[ -d "$VENV" ]] && ! venv_usable; then
    warn "recreating venv"
    python3 -m venv --clear "$VENV"
  elif [[ ! -d "$VENV" ]]; then
    log "creating venv at $VENV"
    python3 -m venv "$VENV"
  fi
  "$PYTHON" -m pip install --upgrade pip >/dev/null
  log "installing requirements"
  "$PYTHON" -m pip install -r "$ROOT/requirements.txt"
}

ensure_installed() {
  deps_installed || install_deps
}

resolve_hermes_cmd() {
  if [[ -n "${SEO_HERMES_CMD:-}" ]]; then
    printf '%s' "$SEO_HERMES_CMD"
    return
  fi
  if command -v hermes >/dev/null 2>&1; then
    printf 'hermes'
    return
  fi
  if [[ -x "$HERMES_AGENT/.venv/bin/hermes" ]]; then
    printf '%s' "$HERMES_AGENT/.venv/bin/hermes"
    return
  fi
  printf 'python3 -m hermes_cli.main'
}

list_port_pids() {
  local port="$1"
  if command -v lsof >/dev/null; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  fi
}

kill_port() {
  local label="$1" port="$2"
  local pids
  pids="$(list_port_pids "$port" | tr '\n' ' ')"
  if [[ -z "${pids// /}" ]]; then
    return 0
  fi
  log "stopping $label on :$port (pids: ${pids// /, })"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  local i
  for i in $(seq 1 10); do
    pids="$(list_port_pids "$port" | tr '\n' ' ')"
    [[ -z "${pids// /}" ]] && return 0
    sleep 0.3
  done
  pids="$(list_port_pids "$port" | tr '\n' ' ')"
  if [[ -n "${pids// /}" ]]; then
    warn "force-killing $label :$port"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

stop_port_graceful() {
  local label="$1" port="$2"
  kill_port "$label" "$port"
}

status_one() {
  local label="$1" port="$2"
  local pids
  pids="$(list_port_pids "$port" | tr '\n' ' ')"
  if [[ -n "${pids// /}" ]]; then
    log "$label port $port: listening (pids: ${pids// /, })"
  else
    warn "$label port $port: not listening"
  fi
}

print_status() {
  load_env
  status_one bridge "$SEO_BRIDGE_PORT"
  status_one gateway "$SEO_GATEWAY_PORT"
  log "profile → $PROFILE ($PROFILE_DIR)"
  log "skill   → $SEO_SKILL_DIR"
  log "studio  → http://127.0.0.1:$SEO_BRIDGE_PORT/"
  log "gateway → $HERMES_GATEWAY_BASE"
}

preflight() {
  [[ -d "$PROFILE_DIR" ]] || die "profile dir missing: $PROFILE_DIR (expected ~/.hermes/profiles/povison-seo)"
  if [[ "$PROFILE" != "povison-seo" ]]; then
    warn "SEO_PROFILE=$PROFILE (default is povison-seo — confirm this is intentional)"
  fi
  [[ -f "$SEO_STUDIO_HTML" ]] || warn "Studio HTML missing: $SEO_STUDIO_HTML (set SEO_STUDIO_HTML)"
  [[ -d "$SEO_SKILL_DIR/scripts" ]] || die "skill scripts missing: $SEO_SKILL_DIR/scripts"
  mkdir -p "$LOG_DIR" "$SEO_RUNS_DIR"
}

wait_for_bridge() {
  local url="http://127.0.0.1:${SEO_BRIDGE_PORT}/api/health"
  local i
  log "waiting for Studio Bridge on :$SEO_BRIDGE_PORT ..."
  for i in $(seq 1 40); do
    if curl -sf --max-time 2 "$url" >/dev/null 2>&1; then
      log "bridge ready ($url)"
      return 0
    fi
    sleep 0.5
  done
  warn "bridge not ready after ~20s — open $url to debug"
  return 1
}

wait_for_gateway() {
  local url="http://127.0.0.1:${SEO_GATEWAY_PORT}/v1/models"
  local auth_key="${API_SERVER_KEY:-${HERMES_GATEWAY_KEY:-}}"
  local curl_args=(curl -sf --max-time 2)
  local i
  log "waiting for Gateway API (profile=$PROFILE) on :$SEO_GATEWAY_PORT ..."
  if [[ -n "$auth_key" ]]; then
    curl_args+=(-H "Authorization: Bearer $auth_key")
  else
    warn "API_SERVER_KEY / HERMES_GATEWAY_KEY unset in profile .env — /v1/runs may 401"
  fi
  for i in $(seq 1 60); do
    if "${curl_args[@]}" "$url" >/dev/null 2>&1; then
      log "gateway API ready ($url)"
      return 0
    fi
    sleep 1
  done
  warn "gateway not ready after 60s — 「请 Agent」将失败直到 gateway 起来"
  return 1
}

start_bridge() {
  ensure_installed
  local log_file="$LOG_DIR/seo-studio-bridge.log"
  log "bridge → http://127.0.0.1:$SEO_BRIDGE_PORT/  (Studio UI)"
  log "bridge skill → $SEO_SKILL_DIR"
  log "bridge → gateway $HERMES_GATEWAY_BASE (profile $PROFILE)"
  log "bridge log → $log_file"
  cd "$ROOT"
  exec env \
    SEO_SKILL_DIR="$SEO_SKILL_DIR" \
    SEO_RUNS_DIR="$SEO_RUNS_DIR" \
    SEO_STUDIO_DIR="$SEO_STUDIO_DIR" \
    SEO_STUDIO_HTML="$SEO_STUDIO_HTML" \
    SEO_BRIDGE_PORT="$SEO_BRIDGE_PORT" \
    HERMES_GATEWAY_BASE="$HERMES_GATEWAY_BASE" \
    HERMES_GATEWAY_KEY="${HERMES_GATEWAY_KEY:-}" \
    SEO_PROFILE="$PROFILE" \
    SEO_LLM_BASE_URL="${SEO_LLM_BASE_URL:-}" \
    SEO_LLM_API_KEY="${SEO_LLM_API_KEY:-}" \
    SEO_LLM_MODEL="${SEO_LLM_MODEL:-glm-5.2}" \
    UNSPLASH_ACCESS_KEY="${UNSPLASH_ACCESS_KEY:-}" \
    PEXELS_API_KEY="${PEXELS_API_KEY:-}" \
    "$PYTHON" -m uvicorn server:app \
      --host "$SEO_BRIDGE_HOST" --port "$SEO_BRIDGE_PORT" >>"$log_file" 2>&1
}

ensure_api_server_key() {
  # API server refuses to bind without a key (even on 127.0.0.1).
  if [[ -n "${API_SERVER_KEY:-}" ]]; then
    export HERMES_GATEWAY_KEY="${HERMES_GATEWAY_KEY:-$API_SERVER_KEY}"
    return 0
  fi
  local key
  key="seo-$(openssl rand -hex 20 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(20))')"
  mkdir -p "$(dirname "$PROFILE_ENV")"
  {
    echo ""
    echo "# Auto-added by seo-studio/start.sh for Gateway API (:$SEO_GATEWAY_PORT)"
    echo "API_SERVER_ENABLED=true"
    echo "API_SERVER_PORT=$SEO_GATEWAY_PORT"
    echo "API_SERVER_KEY=$key"
  } >>"$PROFILE_ENV"
  export API_SERVER_KEY="$key"
  export HERMES_GATEWAY_KEY="$key"
  log "generated API_SERVER_KEY and appended to $PROFILE_ENV"
}

ensure_api_only_gateway_plugins() {
  # Feishu is enabled via plugins + config.yaml credentials and conflicts with
  # other gateways. Disable platforms/feishu for Studio API-only Gateway.
  local cfg="$PROFILE_DIR/config.yaml"
  [[ -f "$cfg" ]] || return 0
  command -v python3 >/dev/null || return 0
  python3 - "$cfg" <<'PY' || true
import sys
from pathlib import Path
try:
    import yaml
except ImportError:
    sys.exit(0)
path = Path(sys.argv[1])
doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
plugins = doc.setdefault("plugins", {})
disabled = list(plugins.get("disabled") or [])
need = "platforms/feishu"
if need in disabled:
    sys.exit(0)
disabled.append(need)
plugins["disabled"] = disabled
path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
print(f"disabled plugin {need}")
PY
}

start_gateway() {
  local hermes_cmd log_file
  ensure_api_server_key
  ensure_api_only_gateway_plugins
  hermes_cmd="$(resolve_hermes_cmd)"
  log_file="$LOG_DIR/gateway.log"
  mkdir -p "$(dirname "$log_file")"
  log "gateway (hermes -p $PROFILE) → http://127.0.0.1:$SEO_GATEWAY_PORT"
  log "gateway HERMES_HOME → $PROFILE_DIR"
  log "gateway mode → API-first (Feishu plugin disabled; QQ env cleared for this process)"
  log "gateway log → $log_file"
  export HERMES_HOME="$PROFILE_DIR"
  export API_SERVER_ENABLED=true
  export API_SERVER_PORT="${SEO_GATEWAY_PORT}"
  export API_SERVER_KEY
  export HERMES_GATEWAY_KEY="${API_SERVER_KEY}"
  # QQ is env-gated — clear for this process only so we don't fight other gateways.
  if [[ "$hermes_cmd" == "python3 -m hermes_cli.main" ]]; then
    (cd "$HERMES_AGENT" && exec env -u QQ_APP_ID -u QQ_CLIENT_SECRET -u QQBOT_APP_ID -u QQBOT_CLIENT_SECRET \
      HERMES_HOME="$PROFILE_DIR" \
      API_SERVER_ENABLED=true API_SERVER_PORT="$SEO_GATEWAY_PORT" \
      API_SERVER_KEY="$API_SERVER_KEY" HERMES_GATEWAY_KEY="$API_SERVER_KEY" \
      python3 -m hermes_cli.main -p "$PROFILE" gateway run --replace) >>"$log_file" 2>&1
  else
    exec env -u QQ_APP_ID -u QQ_CLIENT_SECRET -u QQBOT_APP_ID -u QQBOT_CLIENT_SECRET \
      HERMES_HOME="$PROFILE_DIR" \
      API_SERVER_ENABLED=true API_SERVER_PORT="$SEO_GATEWAY_PORT" \
      API_SERVER_KEY="$API_SERVER_KEY" HERMES_GATEWAY_KEY="$API_SERVER_KEY" \
      "$hermes_cmd" -p "$PROFILE" gateway run --replace >>"$log_file" 2>&1
  fi
}

stop_all() {
  load_env
  log "stopping SEO stack (bridge → gateway)"
  stop_port_graceful bridge "$SEO_BRIDGE_PORT"
  stop_port_graceful gateway "$SEO_GATEWAY_PORT"
  sleep 0.3
}

start_all() {
  load_env
  ensure_api_server_key
  # Re-export so Bridge child inherits the key for Agent calls
  export HERMES_GATEWAY_KEY="${API_SERVER_KEY}"
  export HERMES_GATEWAY_BASE="http://127.0.0.1:$SEO_GATEWAY_PORT"
  preflight
  ensure_installed

  # Free ports before bind (handles leftover uvicorn on 8766)
  kill_port bridge "$SEO_BRIDGE_PORT" || true
  # Gateway --replace usually handles itself; still clear if stale listener
  # (don't kill unrelated gateways on other ports)

  local pids=()
  cleanup() {
    log "shutting down ($*)"
    local p
    for p in "${pids[@]:-}"; do
      kill "$p" 2>/dev/null || true
    done
    local i
    for i in $(seq 1 12); do
      local busy=0
      for p in "${pids[@]:-}"; do
        kill -0 "$p" 2>/dev/null && busy=1
      done
      [[ "$busy" -eq 0 ]] && break
      sleep 1
    done
    for p in "${pids[@]:-}"; do
      kill -9 "$p" 2>/dev/null || true
    done
    # Also clear ports in case children re-bound
    kill_port bridge "$SEO_BRIDGE_PORT" || true
    wait 2>/dev/null || true
  }
  trap 'cleanup EXIT' EXIT
  trap 'cleanup INT; exit 130' INT
  trap 'cleanup TERM; exit 143' TERM

  start_gateway &
  pids+=("$!")
  wait_for_gateway || true

  start_bridge &
  pids+=("$!")
  wait_for_bridge || true

  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log "SEO stack up (profile=$PROFILE)"
  log "  Studio  → http://127.0.0.1:$SEO_BRIDGE_PORT/"
  log "  Gateway → $HERMES_GATEWAY_BASE  (hermes -p $PROFILE)"
  log "  Skill   → $SEO_SKILL_DIR"
  log "  Logs    → $LOG_DIR/"
  log "Ctrl-C to stop."
  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # macOS: open browser
  if [[ "${SEO_OPEN_BROWSER:-1}" == "1" ]] && command -v open >/dev/null 2>&1; then
    open "http://127.0.0.1:$SEO_BRIDGE_PORT/" >/dev/null 2>&1 || true
  fi

  wait
}

mode="${1:-all}"

case "$mode" in
  all|"")
    start_all
    ;;
  restart)
    stop_all
    start_all
    ;;
  stop)
    stop_all
    ;;
  bridge)
    load_env
    preflight
    ensure_installed
    kill_port bridge "$SEO_BRIDGE_PORT" || true
    start_bridge
    ;;
  gateway)
    load_env
    preflight
    start_gateway
    ;;
  install)
    install_deps
    log "install done"
    ;;
  status)
    print_status
    ;;
  open)
    load_env
    log "http://127.0.0.1:$SEO_BRIDGE_PORT/"
    ;;
  *)
    cat <<EOF
Usage: ./start.sh [all|restart|stop|bridge|gateway|install|status|open]
EOF
    exit 1
    ;;
esac
