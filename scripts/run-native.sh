#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
export SMART_THERMOSTAT_API="${SMART_THERMOSTAT_API:-http://127.0.0.1:8080}"
exec /usr/bin/python3 "$APP_DIR/native_app/main.py"
