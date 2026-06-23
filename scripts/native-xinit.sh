#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Runtime logs/caches must not live in the Git checkout. On the Pi this is
# provided by systemd RuntimeDirectory=/run/smart-thermostat-native, which is
# tmpfs. The /tmp fallback keeps manual runs from writing data/logs/*.log.
RUNTIME_DIR="${SMART_THERMOSTAT_RUNTIME_DIR:-/tmp/smart-thermostat-native}"
LOG_DIR="$RUNTIME_DIR/logs"
CACHE_DIR="${XDG_CACHE_HOME:-$RUNTIME_DIR/cache}"
PYCACHE_DIR="${PYTHONPYCACHEPREFIX:-$RUNTIME_DIR/pycache}"
mkdir -p "$LOG_DIR" "$CACHE_DIR" "$PYCACHE_DIR"
chmod 700 "$RUNTIME_DIR" 2>/dev/null || true

LOG_FILE="$LOG_DIR/native-ui.log"
: >"$LOG_FILE"
exec >>"$LOG_FILE" 2>&1

echo "===== Smart Thermostat native X session starting: $(date) ====="
echo "Runtime directory: $RUNTIME_DIR"

export SMART_THERMOSTAT_API="${SMART_THERMOSTAT_API:-http://127.0.0.1:8080}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export XDG_CACHE_HOME="$CACHE_DIR"
export PYTHONPYCACHEPREFIX="$PYCACHE_DIR"
export QT_LOGGING_RULES="${QT_LOGGING_RULES:-*.debug=false;qt.qpa.*=false}"

# Keep appliance display awake.
xset s off || true
xset -dpms || true
xset s noblank || true

# Hide cursor. Touch still works.
if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.1 -root &
fi

# Native DSI display setup.
# DSI panel reports 800x1280 physically, but the UI is landscape.
# Current panel is flipped 180 degrees from the previous install to move the
# broken touchscreen edge to the opposite side. Override with:
#   SMART_THERMOSTAT_SCREEN_ROTATION=left|right|normal|inverted
DISPLAY_OUTPUT="${SMART_THERMOSTAT_DISPLAY_OUTPUT:-DSI-1}"
SCREEN_ROTATION="${SMART_THERMOSTAT_SCREEN_ROTATION:-right}"
if command -v xrandr >/dev/null 2>&1; then
  xrandr --output "$DISPLAY_OUTPUT" --rotate "$SCREEN_ROTATION" || true
fi

# Goodix touchscreen calibration.
# IMPORTANT:
# Reset the built-in libinput calibration first, then map touch to rotated DSI-1.
# Without resetting libinput Calibration Matrix, Coordinate Transformation Matrix changes are fought by libinput.
if command -v xinput >/dev/null 2>&1; then
  TOUCH_ID="$(xinput list | awk -F'id=' '/Goodix Capacitive TouchScreen/ && /slave  pointer/ {split($2,a,"[ \t]"); print a[1]; exit}')"
  if [ -n "${TOUCH_ID:-}" ]; then
    echo "Configuring Goodix touch pointer id: $TOUCH_ID"
    xinput set-prop "$TOUCH_ID" "libinput Calibration Matrix" 1 0 0 0 1 0 0 0 1 || true
    xinput set-prop "$TOUCH_ID" "Coordinate Transformation Matrix" 1 0 0 0 1 0 0 0 1 || true
    xinput map-to-output "$TOUCH_ID" "$DISPLAY_OUTPUT" || true
    xinput list-props "$TOUCH_ID" | grep -Ei "Coordinate Transformation Matrix|libinput Calibration Matrix|Device Node" || true
  else
    echo "Goodix touch pointer device not found."
  fi
fi

cd "$APP_DIR"
exec /usr/bin/python3 "$APP_DIR/native_app/main.py"
