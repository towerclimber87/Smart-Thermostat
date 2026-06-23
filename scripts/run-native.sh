#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${SMART_THERMOSTAT_RUNTIME_DIR:-/tmp/smart-thermostat-native}"
mkdir -p "$RUNTIME_DIR/cache" "$RUNTIME_DIR/pycache"
cd "$APP_DIR"
export SMART_THERMOSTAT_API="${SMART_THERMOSTAT_API:-http://127.0.0.1:8080}"
export SMART_THERMOSTAT_RUNTIME_DIR="$RUNTIME_DIR"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$RUNTIME_DIR/cache}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-$RUNTIME_DIR/pycache}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QT_X11_NO_MITSHM="${QT_X11_NO_MITSHM:-1}"
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
export PYTHONUNBUFFERED=1
exec /usr/bin/python3 "$APP_DIR/native_app/main.py"
