#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PANEL_URL="${SMART_HYBRID_URL:-${SMART_THERMOSTAT_URL:-${SMART_THERMOSTAT_API:-http://127.0.0.1:8080}}}"
API_URL="${SMART_THERMOSTAT_API:-${PANEL_URL}}"
HEALTH_TIMEOUT_SECONDS="${SMART_HYBRID_HEALTH_TIMEOUT_SECONDS:-75}"
DISPLAY_ROTATION="${SMART_HYBRID_ROTATION:-${SMART_NATIVE_ROTATION:-left}}"
TOUCH_MATRIX="${SMART_HYBRID_TOUCH_MATRIX:-${SMART_NATIVE_TOUCH_MATRIX:--1 0 1 0 -1 1 0 0 1}}"
APP="${PROJECT_DIR}/native/html_panel.py"

ensure_runtime_dir() {
  local runtime_dir="${XDG_RUNTIME_DIR:-}"
  if [[ -z "${runtime_dir}" || ! -d "${runtime_dir}" || ! -w "${runtime_dir}" ]]; then
    runtime_dir="/tmp/smart-thermostat-runtime-$(id -u)"
    mkdir -p "${runtime_dir}"
    chmod 700 "${runtime_dir}"
    export XDG_RUNTIME_DIR="${runtime_dir}"
  fi
}

wait_for_api() {
  local health_url="${API_URL%/}/api/health"
  local started now
  started="$(date +%s)"
  while true; do
    if python3 - "$health_url" <<'PY' >/dev/null 2>&1
import sys
from urllib import request
request.urlopen(sys.argv[1], timeout=1).read()
PY
    then
      return 0
    fi
    now="$(date +%s)"
    if (( now - started >= HEALTH_TIMEOUT_SECONDS )); then
      echo "Hybrid webview API health check timed out after ${HEALTH_TIMEOUT_SECONDS}s: ${health_url}" >&2
      return 1
    fi
    sleep 1
  done
}

wait_for_display() {
  ensure_runtime_dir
  export XAUTHORITY="${XAUTHORITY:-/home/${USER}/.Xauthority}"
  local started now display_number
  started="$(date +%s)"
  while true; do
    if [[ -n "${DISPLAY:-}" ]]; then
      display_number="${DISPLAY#:}"
      display_number="${display_number%%.*}"
      if [[ -S "/tmp/.X11-unix/X${display_number}" ]]; then
        return 0
      fi
    fi
    if [[ -z "${DISPLAY:-}" && -S "/tmp/.X11-unix/X0" ]]; then
      export DISPLAY=":0"
      return 0
    fi
    now="$(date +%s)"
    if (( now - started >= HEALTH_TIMEOUT_SECONDS )); then
      echo "No X11 display was available after ${HEALTH_TIMEOUT_SECONDS}s." >&2
      return 1
    fi
    sleep 1
  done
}

apply_display_calibration() {
  [[ -n "${DISPLAY:-}" ]] || return 0
  if command -v xset >/dev/null 2>&1; then
    xset s off >/dev/null 2>&1 || true
    xset -dpms >/dev/null 2>&1 || true
    xset s noblank >/dev/null 2>&1 || true
  fi
  if command -v xrandr >/dev/null 2>&1 && [[ "${DISPLAY_ROTATION}" != "none" ]]; then
    case "${DISPLAY_ROTATION}" in
      left|right|inverted|normal)
        xrandr --fb 1280x800 --output DSI-1 --mode 800x1280 --rotate "${DISPLAY_ROTATION}" >/dev/null 2>&1 || true
        ;;
    esac
  fi
  if command -v xinput >/dev/null 2>&1 && [[ -n "${TOUCH_MATRIX}" ]]; then
    local touch_id attempt
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
      touch_id="$(xinput list 2>/dev/null | awk '/Goodix Capacitive TouchScreen/ && /slave[[:space:]]+pointer/ { for (i=1; i<=NF; i++) if ($i ~ /^id=/) { sub(/^id=/, "", $i); print $i; exit } }')"
      if [[ -n "${touch_id}" ]]; then
        # shellcheck disable=SC2086
        xinput set-prop "${touch_id}" "Coordinate Transformation Matrix" ${TOUCH_MATRIX} >/dev/null 2>&1 && break
      fi
      sleep 1
    done
  fi
  if command -v unclutter >/dev/null 2>&1; then
    pkill -u "${USER}" -x unclutter >/dev/null 2>&1 || true
    unclutter -idle 0.25 -root >/dev/null 2>&1 &
  fi
}

main() {
  ensure_runtime_dir
  wait_for_api
  wait_for_display
  apply_display_calibration
  export SMART_THERMOSTAT_API="${API_URL}"
  export SMART_THERMOSTAT_API="${API_URL}"
  export SMART_THERMOSTAT_URL="${PANEL_URL}"
  export SMART_HYBRID_URL="${PANEL_URL}"
  export XDG_CACHE_HOME="${SMART_HYBRID_CACHE_DIR:-/tmp/smart-thermostat-webkit-cache-$(id -u)}"
  export XDG_DATA_HOME="${SMART_HYBRID_DATA_DIR:-/tmp/smart-thermostat-webkit-data-$(id -u)}"
  mkdir -p "${XDG_CACHE_HOME}" "${XDG_DATA_HOME}"
  chmod 700 "${XDG_CACHE_HOME}" "${XDG_DATA_HOME}" 2>/dev/null || true
  exec /usr/bin/python3 "${APP}"
}

main "$@"
