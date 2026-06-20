#!/usr/bin/env bash
set -euo pipefail

RAW_PANEL_URL="${SMART_THERMOSTAT_URL:-http://127.0.0.1:8080}"
PROFILE_DIR="${SMART_KIOSK_PROFILE_DIR:-/tmp/smart-thermostat-chromium-profile}"
CACHE_DIR="${SMART_KIOSK_CACHE_DIR:-/tmp/smart-thermostat-chromium-cache}"
HEALTH_TIMEOUT_SECONDS="${SMART_KIOSK_HEALTH_TIMEOUT_SECONDS:-45}"
EXTRA_FLAGS="${SMART_KIOSK_EXTRA_FLAGS:-}"
OZONE_PLATFORM="${SMART_KIOSK_OZONE_PLATFORM:-x11}"
DISPLAY_ROTATION="${SMART_KIOSK_ROTATION:-none}"
TOUCH_MATRIX="${SMART_KIOSK_TOUCH_MATRIX:-}"

with_wall_query() {
  python3 - "$RAW_PANEL_URL" <<'PY'
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

url = sys.argv[1].rstrip("/") or "http://127.0.0.1:8080"
split = urlsplit(url)
query = dict(parse_qsl(split.query, keep_blank_values=True))
query.setdefault("runtime", "chromium-kiosk")
query.setdefault("wall", "1")
query.setdefault("performance", "1")
print(urlunsplit((split.scheme, split.netloc, split.path or "/", urlencode(query), split.fragment)))
PY
}

PANEL_URL="$(with_wall_query)"

find_chromium() {
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
  local health_url="${RAW_PANEL_URL%/}/api/health"
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
  local runtime_dir="${XDG_RUNTIME_DIR:-}"
  if [[ -z "${runtime_dir}" || ! -d "${runtime_dir}" || ! -w "${runtime_dir}" ]]; then
    runtime_dir="/tmp/smart-thermostat-runtime-$(id -u)"
    mkdir -p "${runtime_dir}"
    chmod 700 "${runtime_dir}"
    export XDG_RUNTIME_DIR="${runtime_dir}"
  fi
  export XAUTHORITY="${XAUTHORITY:-/home/${USER}/.Xauthority}"

  local started
  started="$(date +%s)"
  while true; do
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
      echo "No X11 display was available after ${HEALTH_TIMEOUT_SECONDS}s." >&2
      return 1
    fi
    sleep 1
  done
}

apply_display_calibration() {
  wait_for_display

  if command -v xset >/dev/null 2>&1; then
    xset s off >/dev/null 2>&1 || true
    xset -dpms >/dev/null 2>&1 || true
    xset s noblank >/dev/null 2>&1 || true
    xset dpms 0 0 0 >/dev/null 2>&1 || true
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
  wait_for_panel
  apply_display_calibration

  local chromium_bin
  chromium_bin="$(find_chromium)" || {
    echo "Chromium is not installed. Run ./scripts/install-pi.sh or sudo apt-get install chromium-browser/chromium." >&2
    exit 1
  }

  # Keep Chromium's write-heavy profile/cache in RAM.  The project does not need
  # persistent browser state because settings are stored by the local API.
  mkdir -p "${PROFILE_DIR}" "${CACHE_DIR}"
  chmod 700 "${PROFILE_DIR}" "${CACHE_DIR}" 2>/dev/null || true
  rm -f "${PROFILE_DIR}/SingletonLock" "${PROFILE_DIR}/SingletonSocket" "${PROFILE_DIR}/SingletonCookie" 2>/dev/null || true

  local flags=(
    "--app=${PANEL_URL}"
    "--kiosk"
    "--start-fullscreen"
    "--window-size=1280,800"
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
    "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication,InterestFeedContentSuggestions,TabHoverCardImages,AutofillEnableAccountWalletStorage"
    "--disable-print-preview"
    "--disable-speech-api"
    "--disable-sync"
    "--disable-breakpad"
    "--disable-crash-reporter"
    "--disable-logging"
    "--log-level=3"
    "--metrics-recording-only"
    "--password-store=basic"
    "--disk-cache-dir=${CACHE_DIR}"
    "--disk-cache-size=33554432"
    "--media-cache-size=16777216"
    "--user-data-dir=${PROFILE_DIR}"
    "--overscroll-history-navigation=0"
    "--autoplay-policy=no-user-gesture-required"
    "--check-for-update-interval=31536000"
    "--touch-events=enabled"
    "--enable-gpu-rasterization"
    "--enable-zero-copy"
    "--enable-features=OverlayScrollbar"
  )

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
