#!/usr/bin/env bash
set -euo pipefail

PANEL_URL="${SMART_THERMOSTAT_URL:-http://127.0.0.1:8080}"
PROFILE_DIR="${SMART_KIOSK_PROFILE_DIR:-/tmp/smart-thermostat-chromium-profile}"
CACHE_DIR="${SMART_KIOSK_CACHE_DIR:-/tmp/smart-thermostat-chromium-cache}"
HEALTH_TIMEOUT_SECONDS="${SMART_KIOSK_HEALTH_TIMEOUT_SECONDS:-45}"
EXTRA_FLAGS="${SMART_KIOSK_EXTRA_FLAGS:-}"
OZONE_PLATFORM="${SMART_KIOSK_OZONE_PLATFORM:-auto}"
LOW_POWER_MODE="${SMART_KIOSK_LOW_POWER_MODE:-1}"
DISPLAY_ROTATION="${SMART_KIOSK_ROTATION:-left}"
TOUCH_MATRIX="${SMART_KIOSK_TOUCH_MATRIX:--1 0 1 0 -1 1 0 0 1}"

ensure_runtime_dir() {
  local runtime_dir="${XDG_RUNTIME_DIR:-}"
  if [[ -z "${runtime_dir}" || ! -d "${runtime_dir}" || ! -w "${runtime_dir}" ]]; then
    runtime_dir="/tmp/smart-thermostat-runtime-$(id -u)"
    mkdir -p "${runtime_dir}"
    chmod 700 "${runtime_dir}"
    export XDG_RUNTIME_DIR="${runtime_dir}"
  fi
}

find_chromium() {
  if [[ -x /usr/lib/chromium/chromium ]]; then
    echo /usr/lib/chromium/chromium
    return 0
  fi
  if command -v chromium-browser >/dev/null 2>&1; then
    command -v chromium-browser
    return 0
  fi
  if command -v chromium >/dev/null 2>&1; then
    command -v chromium
    return 0
  fi
  if command -v google-chrome >/dev/null 2>&1; then
    command -v google-chrome
    return 0
  fi
  return 1
}

wait_for_panel() {
  local health_url="${PANEL_URL%/}/api/health"
  local started
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

    local now
    now="$(date +%s)"
    if (( now - started >= HEALTH_TIMEOUT_SECONDS )); then
      echo "Panel health check timed out after ${HEALTH_TIMEOUT_SECONDS}s: ${health_url}" >&2
      return 1
    fi
    sleep 1
  done
}

wait_for_display() {
  ensure_runtime_dir
  export XAUTHORITY="${XAUTHORITY:-/home/${USER}/.Xauthority}"

  local started
  started="$(date +%s)"
  while true; do
    if [[ -n "${WAYLAND_DISPLAY:-}" && -S "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" ]]; then
      return 0
    fi
    if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
      local wayland_socket
      for wayland_socket in "${XDG_RUNTIME_DIR}"/wayland-*; do
        if [[ -S "${wayland_socket}" ]]; then
          export WAYLAND_DISPLAY="${wayland_socket##*/}"
          return 0
        fi
      done
    fi
    if [[ -n "${DISPLAY:-}" ]]; then
      local display_number="${DISPLAY#:}"
      display_number="${display_number%%.*}"
      if [[ -S "/tmp/.X11-unix/X${display_number}" ]]; then
        return 0
      fi
    fi
    if [[ -z "${DISPLAY:-}" && -S "/tmp/.X11-unix/X0" ]]; then
      export DISPLAY=":0"
      return 0
    fi

    local now
    now="$(date +%s)"
    if (( now - started >= HEALTH_TIMEOUT_SECONDS )); then
      echo "No desktop display was available after ${HEALTH_TIMEOUT_SECONDS}s." >&2
      echo "Make sure the Pi boots to the graphical desktop and the kiosk user is logged in." >&2
      return 1
    fi
    sleep 1
  done
}


apply_x11_display_calibration() {
  [[ -n "${DISPLAY:-}" ]] || return 0

  # The Waveshare 10.1" DSI panel is native portrait.  Our wall layout is
  # landscape counterclockwise, so rotate at the X11 layer after X starts.
  if command -v xrandr >/dev/null 2>&1 && [[ "${DISPLAY_ROTATION}" != "none" ]]; then
    case "${DISPLAY_ROTATION}" in
      left|right|inverted|normal)
        xrandr --fb 1280x800 --output DSI-1 --mode 800x1280 --rotate "${DISPLAY_ROTATION}" >/dev/null 2>&1 || true
        ;;
    esac
  fi

  # xinput sees the Goodix device twice: one pointer and one keyboard entry.
  # Only the slave pointer entry accepts the Coordinate Transformation Matrix.
  if command -v xinput >/dev/null 2>&1 && [[ -n "${TOUCH_MATRIX}" ]]; then
    local touch_id=""
    local attempt
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
      touch_id="$(xinput list 2>/dev/null | awk '/Goodix Capacitive TouchScreen/ && /slave[[:space:]]+pointer/ { for (i=1; i<=NF; i++) if ($i ~ /^id=/) { sub(/^id=/, "", $i); print $i; exit } }')"
      if [[ -n "${touch_id}" ]]; then
        # shellcheck disable=SC2086
        xinput set-prop "${touch_id}" "Coordinate Transformation Matrix" ${TOUCH_MATRIX} >/dev/null 2>&1 && break
      fi
      sleep 1
    done
  fi
}

prepare_display() {
  wait_for_display

  if [[ -n "${DISPLAY:-}" ]] && command -v xset >/dev/null 2>&1; then
    xset s off >/dev/null 2>&1 || true
    xset -dpms >/dev/null 2>&1 || true
    xset s noblank >/dev/null 2>&1 || true
  fi

  apply_x11_display_calibration

  if [[ -n "${DISPLAY:-}" ]] && command -v unclutter >/dev/null 2>&1; then
    pkill -u "${USER}" -x unclutter >/dev/null 2>&1 || true
    unclutter -idle 0.25 -root >/dev/null 2>&1 &
  fi
}

main() {
  ensure_runtime_dir
  wait_for_panel
  prepare_display

  local chromium_bin
  chromium_bin="$(find_chromium)" || {
    echo "Chromium is not installed. Run scripts/install-pi.sh or install chromium-browser/chromium." >&2
    exit 1
  }

  mkdir -p "${PROFILE_DIR}" "${CACHE_DIR}"
  rm -f "${PROFILE_DIR}/SingletonLock" "${PROFILE_DIR}/SingletonSocket" "${PROFILE_DIR}/SingletonCookie" 2>/dev/null || true

  local flags=(
    "--app=${PANEL_URL}"
    "--kiosk"
    "--start-fullscreen"
    "--no-first-run"
    "--no-default-browser-check"
    "--noerrdialogs"
    "--disable-infobars"
    "--disable-session-crashed-bubble"
    "--disable-translate"
    "--disable-background-networking"
    "--disable-component-update"
    "--disable-default-apps"
    "--disable-domain-reliability"
    "--disable-extensions"
    "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication,InterestFeedContentSuggestions,TabHoverCardImages"
    "--disable-print-preview"
    "--disable-speech-api"
    "--disable-sync"
    "--metrics-recording-only"
    "--password-store=basic"
    "--disk-cache-dir=${CACHE_DIR}"
    "--disk-cache-size=33554432"
    "--media-cache-size=16777216"
    "--user-data-dir=${PROFILE_DIR}"
    "--overscroll-history-navigation=0"
    "--autoplay-policy=no-user-gesture-required"
    "--check-for-update-interval=31536000"
  )

  if [[ "${LOW_POWER_MODE}" == "1" || "${LOW_POWER_MODE}" == "true" || "${LOW_POWER_MODE}" == "yes" ]]; then
    flags+=(
      "--disable-gpu"
      "--disable-gpu-compositing"
      "--disable-gpu-rasterization"
      "--disable-accelerated-2d-canvas"
      "--disable-webgl"
      "--disable-3d-apis"
      "--disable-smooth-scrolling"
      "--renderer-process-limit=2"
      "--process-per-site"
    )
  fi

  if [[ "${OZONE_PLATFORM}" == "wayland" ]] || { [[ "${OZONE_PLATFORM}" == "auto" ]] && [[ -n "${WAYLAND_DISPLAY:-}" ]]; }; then
    flags+=("--ozone-platform=wayland")
  elif [[ "${OZONE_PLATFORM}" == "x11" ]]; then
    flags+=("--ozone-platform=x11")
  fi

  if [[ -n "${EXTRA_FLAGS}" ]]; then
    # shellcheck disable=SC2206
    local extra=( ${EXTRA_FLAGS} )
    flags+=("${extra[@]}")
  fi

  exec "${chromium_bin}" "${flags[@]}"
}

main "$@"
