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

# Hide mouse cursor if available. Touch still works.
if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.1 -root >/dev/null 2>&1 &
fi

# No browser. No window manager required for a single full-screen Qt app.
cd "$APP_DIR"
echo "$(date -Is) launching native UI from $APP_DIR" >> "$LOG_DIR/native-ui.log"
exec /usr/bin/python3 "$APP_DIR/native_app/main.py" >> "$LOG_DIR/native-ui.log" 2>&1
