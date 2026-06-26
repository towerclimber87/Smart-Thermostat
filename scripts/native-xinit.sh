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

# Keep X from blanking the appliance display on its own, but leave DPMS
# available so the native app can intentionally put the panel to sleep after
# inactivity or from the on-screen sleep button.
xset s off || true
xset s noblank || true
xset +dpms || true
xset dpms 0 0 0 || true

# Hide cursor. Touch still works.
if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.1 -root &
fi

# Native DSI display + touch orientation.
# The DSI panel reports 800x1280 physically, but the UI is landscape.
# scripts/apply-screen-orientation.sh reads data/panel-config.json and maps:
#   Upright     -> xrandr right
#   Upside Down -> xrandr left
# It also remaps the touchscreen after rotation so touch follows the display.
"$APP_DIR/scripts/apply-screen-orientation.sh" || true

cd "$APP_DIR"
exec /usr/bin/python3 "$APP_DIR/native_app/main.py"
