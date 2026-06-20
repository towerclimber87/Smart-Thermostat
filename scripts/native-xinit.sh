#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Keep the appliance display awake and clean.
xset s off || true
xset -dpms || true
xset s noblank || true

# Hide mouse cursor if available. Touch still works.
if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.1 -root &
fi

# No browser. No window manager required for a single full-screen Qt app.
cd "$APP_DIR"
exec /usr/bin/python3 "$APP_DIR/native_app/main.py"
