#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$APP_DIR/data/logs"
mkdir -p "$LOG_DIR"

exec >>"$LOG_DIR/native-ui.log" 2>&1

echo "===== Smart Thermostat native X session starting: $(date) ====="

export SMART_THERMOSTAT_API="${SMART_THERMOSTAT_API:-http://127.0.0.1:8080}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

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
if command -v xrandr >/dev/null 2>&1; then
  xrandr --output DSI-1 --rotate left || true
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
    xinput map-to-output "$TOUCH_ID" DSI-1 || true
    xinput list-props "$TOUCH_ID" | grep -Ei "Coordinate Transformation Matrix|libinput Calibration Matrix|Device Node" || true
  else
    echo "Goodix touch pointer device not found."
  fi
fi

cd "$APP_DIR"
exec /usr/bin/python3 "$APP_DIR/native_app/main.py"
