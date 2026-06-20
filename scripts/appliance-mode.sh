#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

INSTALL_USER="$(id -un)"
INSTALL_UID="$(id -u)"

install_service() {
  local service_name="$1"
  local source_path="${PROJECT_DIR}/systemd/${service_name}"
  local service_path="/etc/systemd/system/${service_name}"
  if [[ ! -f "${source_path}" ]]; then
    return 0
  fi
  sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
      -e "s|__USER__|${INSTALL_USER}|g" \
      -e "s|__UID__|${INSTALL_UID}|g" \
      "${source_path}" > "/tmp/${service_name}"
  sudo cp "/tmp/${service_name}" "${service_path}"
}


sudo install -d -m 700 /etc/smart-thermostat

# Default wall display runtime:
#   Fast Chromium kiosk under bare X11.
#
# This intentionally goes back to Chromium for the physical panel because the
# GTK/WebKit hybrid can peg the Pi's CPU on the glass/animation-heavy web UI.
# The profile and cache stay in /tmp, so this keeps SD-card writes low while
# preserving the exact clean HTML UI from http://127.0.0.1:8080.
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

# Keep the GTK/WebKit HTML host available as a fallback mode, but do not use it
# as the default appliance display. On this Pi it can run slower than Chromium.
sudo tee /etc/smart-thermostat/hybrid.env >/dev/null <<'EOF'
SMART_THERMOSTAT_URL=http://127.0.0.1:8080
SMART_THERMOSTAT_API=http://127.0.0.1:8080
SMART_HYBRID_HEALTH_TIMEOUT_SECONDS=75
SMART_HYBRID_ROTATION=left
SMART_HYBRID_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
SMART_HYBRID_FULLSCREEN=1
SMART_HYBRID_WIDTH=1280
SMART_HYBRID_HEIGHT=800
SMART_HYBRID_CACHE_DIR=/tmp/smart-thermostat-webkit-cache
SMART_HYBRID_DATA_DIR=/tmp/smart-thermostat-webkit-data
SMART_HYBRID_TITLE=Smart Thermostat
EOF
sudo chmod 600 /etc/smart-thermostat/hybrid.env

# Keep the old Tk/canvas native fallback configured, but do not use it by default.
sudo tee /etc/smart-thermostat/native.env >/dev/null <<'EOF'
SMART_THERMOSTAT_API=http://127.0.0.1:8080
SMART_NATIVE_HEALTH_TIMEOUT_SECONDS=75
SMART_NATIVE_ROTATION=left
SMART_NATIVE_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
SMART_NATIVE_POLL_MS=1500
SMART_NATIVE_SLOW_POLL_MS=8000
SMART_NATIVE_FRAME_MS=500
SMART_NATIVE_DISPLAY_LABEL=Native touchscreen + local web API
SMART_NATIVE_THERMOSTAT_ONLY=0
SMART_NATIVE_VISUAL_MODE=web_full_parity
SMART_NATIVE_FALLBACK_SCHEDULES=1
EOF
sudo chmod 600 /etc/smart-thermostat/native.env

sudo systemctl disable --now display-manager.service >/dev/null 2>&1 || true
sudo systemctl disable --now lightdm.service >/dev/null 2>&1 || true
sudo systemctl disable --now gdm.service >/dev/null 2>&1 || true
sudo systemctl disable --now smart-thermostat-native.service smart-thermostat-hybrid.service >/dev/null 2>&1 || true
sudo systemctl set-default multi-user.target >/dev/null 2>&1 || true

sudo rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 2>/dev/null || true
chmod +x "${PROJECT_DIR}/scripts/kiosk-launch.sh" "${PROJECT_DIR}/scripts/kiosk-xinit.sh" "${PROJECT_DIR}/scripts/hybrid-launch.sh" "${PROJECT_DIR}/scripts/hybrid-xinit.sh" "${PROJECT_DIR}/scripts/native-launch.sh" "${PROJECT_DIR}/scripts/native-xinit.sh" "${PROJECT_DIR}/native/html_panel.py" 2>/dev/null || true

install_service smart-thermostat-web.service
install_service smart-thermostat-kiosk.service
install_service smart-thermostat-hybrid.service
install_service smart-thermostat-native.service

sudo systemctl daemon-reload
sudo systemctl enable smart-thermostat-web.service smart-thermostat-kiosk.service >/dev/null 2>&1 || true
sudo systemctl restart smart-thermostat-web.service smart-thermostat-kiosk.service

echo "Smart Thermostat fast HTML appliance mode enabled: the wall display shows the same port-8080 web UI in optimized Chromium under bare X11. Profile/cache are in /tmp to avoid SD-card hammering."
