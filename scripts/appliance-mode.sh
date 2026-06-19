#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sudo install -d -m 700 /etc/smart-thermostat
sudo tee /etc/smart-thermostat/native.env >/dev/null <<'EOF'
SMART_THERMOSTAT_API=http://127.0.0.1:8080
SMART_NATIVE_HEALTH_TIMEOUT_SECONDS=75
SMART_NATIVE_ROTATION=left
SMART_NATIVE_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
SMART_NATIVE_POLL_MS=1500
SMART_NATIVE_SLOW_POLL_MS=8000
SMART_NATIVE_THERMOSTAT_ONLY=1
SMART_NATIVE_VISUAL_MODE=web_parity
EOF
sudo chmod 600 /etc/smart-thermostat/native.env

# Keep the old Chromium kiosk env available for fallback, but do not use it as
# the appliance display service.
sudo tee /etc/smart-thermostat/kiosk.env >/dev/null <<'EOF'
SMART_THERMOSTAT_URL=http://127.0.0.1:8080
SMART_KIOSK_PROFILE_DIR=/tmp/smart-thermostat-chromium-profile
SMART_KIOSK_CACHE_DIR=/tmp/smart-thermostat-chromium-cache
SMART_KIOSK_HEALTH_TIMEOUT_SECONDS=75
SMART_KIOSK_OZONE_PLATFORM=x11
SMART_KIOSK_LOW_POWER_MODE=1
SMART_KIOSK_ROTATION=left
SMART_KIOSK_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
SMART_KIOSK_EXTRA_FLAGS=
EOF
sudo chmod 600 /etc/smart-thermostat/kiosk.env

sudo systemctl disable --now display-manager.service >/dev/null 2>&1 || true
sudo systemctl disable --now lightdm.service >/dev/null 2>&1 || true
sudo systemctl disable --now gdm.service >/dev/null 2>&1 || true
sudo systemctl disable --now smart-thermostat-kiosk.service >/dev/null 2>&1 || true
sudo systemctl set-default multi-user.target >/dev/null 2>&1 || true

sudo rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 2>/dev/null || true
chmod +x "${PROJECT_DIR}/scripts/native-launch.sh" "${PROJECT_DIR}/scripts/native-xinit.sh" "${PROJECT_DIR}/scripts/kiosk-launch.sh" "${PROJECT_DIR}/scripts/kiosk-xinit.sh" 2>/dev/null || true

sudo systemctl daemon-reload
sudo systemctl enable smart-thermostat-web.service smart-thermostat-native.service >/dev/null 2>&1 || true
sudo systemctl restart smart-thermostat-web.service smart-thermostat-native.service

echo "Smart Thermostat native appliance mode enabled: desktop/Chromium kiosk disabled, native display owns tty7."
