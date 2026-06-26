#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Runtime logs/caches must not live in the Git checkout. Caches stay under the
# systemd RuntimeDirectory. Logs go to /dev/shm by default so they survive a
# native service restart without writing normal activity to the SD card.
RUNTIME_DIR="${SMART_THERMOSTAT_RUNTIME_DIR:-/tmp/smart-thermostat-native}"
DEFAULT_LOG_DIR="/dev/shm/smart-thermostat-native/logs"
if [ ! -d /dev/shm ] || [ ! -w /dev/shm ]; then
  DEFAULT_LOG_DIR="$RUNTIME_DIR/logs"
fi
LOG_DIR="${SMART_THERMOSTAT_LOG_DIR:-$DEFAULT_LOG_DIR}"
CACHE_DIR="${XDG_CACHE_HOME:-$RUNTIME_DIR/cache}"
PYCACHE_DIR="${PYTHONPYCACHEPREFIX:-$RUNTIME_DIR/pycache}"
mkdir -p "$LOG_DIR" "$CACHE_DIR" "$PYCACHE_DIR"
chmod 700 "$RUNTIME_DIR" 2>/dev/null || true
chmod 700 "$(dirname "$LOG_DIR")" "$LOG_DIR" 2>/dev/null || true

LOG_FILE="$LOG_DIR/native-ui.log"
PREVIOUS_LOG_FILE="$LOG_DIR/native-ui.previous.log"
# Do not truncate the only clue after a crash/restart. Keep the previous run and
# cap the current RAM log so it cannot grow forever during long uptimes.
if [ -f "$LOG_FILE" ]; then
  tail -n 1600 "$LOG_FILE" >"$PREVIOUS_LOG_FILE" 2>/dev/null || true
  tail -n 800 "$LOG_FILE" >"$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE" || true
fi
exec >>"$LOG_FILE" 2>&1

echo "===== Smart Thermostat native X session starting: $(date) ====="
echo "Runtime directory: $RUNTIME_DIR"
echo "Log directory: $LOG_DIR"

export SMART_THERMOSTAT_API="${SMART_THERMOSTAT_API:-http://127.0.0.1:8080}"
export SMART_THERMOSTAT_LOG_DIR="$LOG_DIR"
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
