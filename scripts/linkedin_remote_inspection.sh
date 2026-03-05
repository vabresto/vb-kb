#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${ROOT_DIR}/.build/enrichment/remote-inspection"
LOG_DIR="${RUNTIME_DIR}/logs"
PID_DIR="${RUNTIME_DIR}/pids"

DISPLAY_ID=":99"
DAEMON_HOST="127.0.0.1"
DAEMON_PORT="8771"
VNC_PORT="5901"
NOVNC_PORT="6081"
SESSION_STATE="${ROOT_DIR}/.build/enrichment/sessions/linkedin.com/storage-state.json"
DAEMON_STATE_PATH="${ROOT_DIR}/.build/enrichment/daemon/linkedin-daemon-state.json"
OPEN_CONTROL_TAB="true"

usage() {
  cat <<'EOF'
Usage:
  scripts/linkedin_remote_inspection.sh start [options]
  scripts/linkedin_remote_inspection.sh stop
  scripts/linkedin_remote_inspection.sh status

Options for start:
  --display :99
  --daemon-host 127.0.0.1
  --daemon-port 8771
  --vnc-port 5901
  --novnc-port 6081
  --session-state /abs/path/storage-state.json
  --daemon-state-path /abs/path/linkedin-daemon-state.json
  --open-control-tab true|false
EOF
}

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing required command: $cmd" >&2
    return 1
  fi
}

pid_is_running() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  kill -0 "$pid" >/dev/null 2>&1
}

stop_process() {
  local name="$1"
  local pid_file="$2"
  if ! pid_is_running "$pid_file"; then
    rm -f "$pid_file"
    return 0
  fi
  local pid
  pid="$(cat "$pid_file")"
  kill "$pid" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      rm -f "$pid_file"
      return 0
    fi
    sleep 0.2
  done
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$pid_file"
  echo "force-killed $name (pid=$pid)"
}

find_novnc_web_dir() {
  if [[ -n "${NOVNC_WEB_DIR:-}" && -d "${NOVNC_WEB_DIR}" ]]; then
    echo "${NOVNC_WEB_DIR}"
    return 0
  fi
  local candidates=(
    "/usr/share/novnc"
    "/usr/local/share/novnc"
    "${ROOT_DIR}/infra/novnc"
  )
  for candidate in "${candidates[@]}"; do
    if [[ -f "${candidate}/vnc.html" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  echo ""
}

start_stack() {
  require_command Xvfb
  require_command x11vnc
  require_command websockify
  require_command uv

  local novnc_web_dir
  novnc_web_dir="$(find_novnc_web_dir)"
  if [[ -z "$novnc_web_dir" ]]; then
    echo "unable to locate noVNC web assets (set NOVNC_WEB_DIR)" >&2
    return 1
  fi

  mkdir -p "$LOG_DIR" "$PID_DIR"

  if [[ ! -f "$SESSION_STATE" ]]; then
    echo "session state not found: $SESSION_STATE" >&2
    return 1
  fi

  local xvfb_pid_file="${PID_DIR}/xvfb.pid"
  local vnc_pid_file="${PID_DIR}/x11vnc.pid"
  local novnc_pid_file="${PID_DIR}/websockify.pid"
  local daemon_pid_file="${PID_DIR}/daemon.pid"

  if ! pid_is_running "$xvfb_pid_file"; then
    Xvfb "$DISPLAY_ID" -screen 0 1920x1080x24 -nolisten tcp >"${LOG_DIR}/xvfb.log" 2>&1 &
    echo $! >"$xvfb_pid_file"
    sleep 0.3
  fi

  if ! pid_is_running "$vnc_pid_file"; then
    DISPLAY="$DISPLAY_ID" x11vnc \
      -display "$DISPLAY_ID" \
      -localhost \
      -rfbport "$VNC_PORT" \
      -shared \
      -forever \
      -nopw >"${LOG_DIR}/x11vnc.log" 2>&1 &
    echo $! >"$vnc_pid_file"
    sleep 0.3
  fi

  if ! pid_is_running "$novnc_pid_file"; then
    websockify --web "$novnc_web_dir" "127.0.0.1:${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}" >"${LOG_DIR}/websockify.log" 2>&1 &
    echo $! >"$novnc_pid_file"
    sleep 0.3
  fi

  if ! pid_is_running "$daemon_pid_file"; then
    local daemon_cmd=(
      uv run --with playwright python "${ROOT_DIR}/scripts/linkedin_playwright_daemon.py"
      --session-state "$SESSION_STATE"
      --state-path "$DAEMON_STATE_PATH"
      --host "$DAEMON_HOST"
      --port "$DAEMON_PORT"
      --headed
    )
    if [[ "$OPEN_CONTROL_TAB" != "true" ]]; then
      daemon_cmd+=(--no-control-tab)
    fi
    DISPLAY="$DISPLAY_ID" "${daemon_cmd[@]}" >"${LOG_DIR}/daemon.log" 2>&1 &
    echo $! >"$daemon_pid_file"
    sleep 0.5
  fi

  echo "remote inspection stack started"
  echo "  control API:  http://${DAEMON_HOST}:${DAEMON_PORT}/api/state"
  echo "  control page: http://${DAEMON_HOST}:${DAEMON_PORT}/control"
  echo "  noVNC URL:    http://127.0.0.1:${NOVNC_PORT}/vnc.html?host=127.0.0.1&port=${NOVNC_PORT}&autoconnect=1&resize=scale"
  echo "  logs:         ${LOG_DIR}"
  echo
  echo "If remote, tunnel ports (example):"
  echo "  ssh -L ${DAEMON_PORT}:127.0.0.1:${DAEMON_PORT} -L ${NOVNC_PORT}:127.0.0.1:${NOVNC_PORT} <host>"
}

stop_stack() {
  local xvfb_pid_file="${PID_DIR}/xvfb.pid"
  local vnc_pid_file="${PID_DIR}/x11vnc.pid"
  local novnc_pid_file="${PID_DIR}/websockify.pid"
  local daemon_pid_file="${PID_DIR}/daemon.pid"
  stop_process "daemon" "$daemon_pid_file"
  stop_process "websockify" "$novnc_pid_file"
  stop_process "x11vnc" "$vnc_pid_file"
  stop_process "xvfb" "$xvfb_pid_file"
  echo "remote inspection stack stopped"
}

status_stack() {
  local xvfb_pid_file="${PID_DIR}/xvfb.pid"
  local vnc_pid_file="${PID_DIR}/x11vnc.pid"
  local novnc_pid_file="${PID_DIR}/websockify.pid"
  local daemon_pid_file="${PID_DIR}/daemon.pid"

  local items=(
    "xvfb:${xvfb_pid_file}"
    "x11vnc:${vnc_pid_file}"
    "websockify:${novnc_pid_file}"
    "daemon:${daemon_pid_file}"
  )
  for item in "${items[@]}"; do
    local name="${item%%:*}"
    local pid_file="${item##*:}"
    if pid_is_running "$pid_file"; then
      echo "$name: running (pid=$(cat "$pid_file"))"
    else
      echo "$name: not running"
    fi
  done
  echo "control API:  http://${DAEMON_HOST}:${DAEMON_PORT}/api/state"
  echo "control page: http://${DAEMON_HOST}:${DAEMON_PORT}/control"
  echo "noVNC URL:    http://127.0.0.1:${NOVNC_PORT}/vnc.html?host=127.0.0.1&port=${NOVNC_PORT}&autoconnect=1&resize=scale"
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

SUBCOMMAND="$1"
shift

if [[ "$SUBCOMMAND" == "start" ]]; then
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --display) DISPLAY_ID="$2"; shift 2 ;;
      --daemon-host) DAEMON_HOST="$2"; shift 2 ;;
      --daemon-port) DAEMON_PORT="$2"; shift 2 ;;
      --vnc-port) VNC_PORT="$2"; shift 2 ;;
      --novnc-port) NOVNC_PORT="$2"; shift 2 ;;
      --session-state) SESSION_STATE="$2"; shift 2 ;;
      --daemon-state-path) DAEMON_STATE_PATH="$2"; shift 2 ;;
      --open-control-tab) OPEN_CONTROL_TAB="$2"; shift 2 ;;
      *) echo "unknown option: $1" >&2; usage; exit 1 ;;
    esac
  done
  start_stack
  exit 0
fi

if [[ "$SUBCOMMAND" == "stop" ]]; then
  stop_stack
  exit 0
fi

if [[ "$SUBCOMMAND" == "status" ]]; then
  status_stack
  exit 0
fi

echo "unknown subcommand: $SUBCOMMAND" >&2
usage
exit 1

