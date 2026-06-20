#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$APP_DIR/data/logs"
mkdir -p "$LOG_DIR"

export SMART_THERMOSTAT_API="${SMART_THERMOSTAT_API:-http://127.0.0.1:8080}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QT_X11_NO_MITSHM="${QT_X11_NO_MITSHM:-1}"
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
export PYTHONUNBUFFERED=1

# Keep the appliance display awake and clean.
xset s off || true
xset -dpms || true
xset s noblank || true

# Rotate the DSI touchscreen before Qt starts so the native app sizes itself
# against the final 1280x800 landscape framebuffer instead of resizing after launch.
PANEL_OUTPUT="${SMART_THERMOSTAT_OUTPUT:-DSI-1}"
PANEL_MODE="${SMART_THERMOSTAT_PANEL_MODE:-800x1280}"
PANEL_FB="${SMART_THERMOSTAT_FB:-1280x800}"
PANEL_ROTATE="${SMART_THERMOSTAT_ROTATE:-left}"
TOUCH_NAME="${SMART_THERMOSTAT_TOUCH_NAME:-Goodix Capacitive TouchScreen}"

if command -v xrandr >/dev/null 2>&1; then
  if ! xrandr --query | grep -q "^${PANEL_OUTPUT} connected"; then
    PANEL_OUTPUT="$(xrandr --query | awk '/ connected/{print $1; exit}')"
  fi
  if [[ -n "${PANEL_OUTPUT:-}" ]]; then
    if xrandr --query | awk -v out="$PANEL_OUTPUT" '
      $1 == out {in_output=1; next}
      in_output && /^[^[:space:]]/ {in_output=0}
      in_output && $1 == "'"$PANEL_MODE"'" {found=1}
      END {exit found ? 0 : 1}
    '; then
      xrandr --output "$PANEL_OUTPUT" --mode "$PANEL_MODE" --rotate "$PANEL_ROTATE" --pos 0x0 --fb "$PANEL_FB" || true
    else
      xrandr --output "$PANEL_OUTPUT" --rotate "$PANEL_ROTATE" --pos 0x0 --fb "$PANEL_FB" || true
    fi
    echo "$(date -Is) display output=$PANEL_OUTPUT mode=$PANEL_MODE fb=$PANEL_FB rotate=$PANEL_ROTATE" >> "$LOG_DIR/native-ui.log"
  fi
fi

# Rotate touch mapping to match xrandr --rotate left.
# Matrix: x' = 1 - y, y' = x
if command -v xinput >/dev/null 2>&1; then
  while read -r touch_id; do
    [[ -z "$touch_id" ]] && continue
    if xinput list-props "$touch_id" 2>/dev/null | grep -q "Coordinate Transformation Matrix"; then
      xinput set-prop "$touch_id" "Coordinate Transformation Matrix" 0 -1 1 1 0 0 0 0 1 || true
      echo "$(date -Is) touch matrix applied to $TOUCH_NAME id=$touch_id" >> "$LOG_DIR/native-ui.log"
    fi
  done < <(xinput list --id-only "$TOUCH_NAME" 2>/dev/null || true)
fi

# Hide mouse cursor if available. Touch still works.
if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.1 -root >/dev/null 2>&1 &
fi

# No browser. No window manager required for a single full-screen Qt app.
cd "$APP_DIR"
echo "$(date -Is) launching native UI from $APP_DIR" >> "$LOG_DIR/native-ui.log"
exec /usr/bin/python3 "$APP_DIR/native_app/main.py" >> "$LOG_DIR/native-ui.log" 2>&1
