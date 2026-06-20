#!/usr/bin/env bash
set -euo pipefail

# Absolute emergency switch for installs that are still showing the old blocky
# Tk/canvas native UI. This keeps the familiar smart-thermostat-native.service
# name, but rewires it to start the HTML WebView host that displays the same UI
# served at http://127.0.0.1:8080.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_USER="$(id -un)"
INSTALL_UID="$(id -u)"
WEB_SERVICE="smart-thermostat-web.service"
NATIVE_SERVICE="smart-thermostat-native.service"
HYBRID_SERVICE="smart-thermostat-hybrid.service"
KIOSK_SERVICE="smart-thermostat-kiosk.service"

if [[ $EUID -eq 0 ]]; then
  echo "Run as the normal user, not root: cd ~/Smart-Thermostat-Development && ./scripts/hard-switch-html-display.sh" >&2
  exit 1
fi

need_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "ERROR: Missing ${path}" >&2
    echo "The 12.1/12.0 hybrid HTML files have not been applied to ${PROJECT_DIR}." >&2
    exit 1
  fi
}

install_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    return 0
  fi
  local packages=(python3 python3-gi xinit openbox x11-xserver-utils unclutter dbus-x11 gir1.2-gtk-3.0)
  if apt-cache show gir1.2-webkit2-4.1 >/dev/null 2>&1; then
    packages+=(gir1.2-webkit2-4.1)
  elif apt-cache show gir1.2-webkit2-4.0 >/dev/null 2>&1; then
    packages+=(gir1.2-webkit2-4.0)
  else
    echo "ERROR: WebKitGTK package was not found by apt." >&2
    exit 1
  fi
  sudo apt-get update
  sudo apt-get install -y "${packages[@]}"
}

render_service() {
  local src="$1"
  local dst="$2"
  sed \
    -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    -e "s|__USER__|${INSTALL_USER}|g" \
    -e "s|__UID__|${INSTALL_UID}|g" \
    "${src}" | sudo tee "${dst}" >/dev/null
}

write_env() {
  sudo install -d -m 700 /etc/smart-thermostat
  sudo tee /etc/smart-thermostat/hybrid.env >/dev/null <<'EOF_ENV'
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
EOF_ENV
  sudo chmod 600 /etc/smart-thermostat/hybrid.env
  sudo cp /etc/smart-thermostat/hybrid.env /etc/smart-thermostat/native.env
  sudo chmod 600 /etc/smart-thermostat/native.env
}

main() {
  cd "${PROJECT_DIR}"
  need_file "${PROJECT_DIR}/native/html_panel.py"
  need_file "${PROJECT_DIR}/scripts/hybrid-xinit.sh"
  need_file "${PROJECT_DIR}/scripts/hybrid-launch.sh"
  need_file "${PROJECT_DIR}/systemd/smart-thermostat-native.service"
  need_file "${PROJECT_DIR}/systemd/smart-thermostat-web.service"

  echo "Installing HTML WebView display packages..."
  install_packages

  echo "Making launchers executable..."
  chmod +x "${PROJECT_DIR}"/scripts/*.sh "${PROJECT_DIR}"/native/*.py 2>/dev/null || true

  echo "Installing service files..."
  render_service "${PROJECT_DIR}/systemd/smart-thermostat-web.service" "/etc/systemd/system/${WEB_SERVICE}"
  render_service "${PROJECT_DIR}/systemd/smart-thermostat-native.service" "/etc/systemd/system/${NATIVE_SERVICE}"
  if [[ -f "${PROJECT_DIR}/systemd/smart-thermostat-hybrid.service" ]]; then
    render_service "${PROJECT_DIR}/systemd/smart-thermostat-hybrid.service" "/etc/systemd/system/${HYBRID_SERVICE}"
  fi
  write_env

  echo "Stopping old blocky display path..."
  sudo systemctl stop "${NATIVE_SERVICE}" "${HYBRID_SERVICE}" "${KIOSK_SERVICE}" >/dev/null 2>&1 || true
  sudo pkill -f "native/thermostat_native.py" >/dev/null 2>&1 || true
  sudo pkill -f "scripts/native-launch.sh" >/dev/null 2>&1 || true
  sudo pkill -f "scripts/kiosk-launch.sh" >/dev/null 2>&1 || true
  sudo pkill -f "chromium.*127.0.0.1:8080" >/dev/null 2>&1 || true
  sudo rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 2>/dev/null || true

  echo "Enabling native service as HTML WebView host..."
  sudo systemctl daemon-reload
  sudo systemctl disable "${KIOSK_SERVICE}" >/dev/null 2>&1 || true
  sudo systemctl disable "${HYBRID_SERVICE}" >/dev/null 2>&1 || true
  sudo systemctl enable --now "${WEB_SERVICE}"
  sudo systemctl enable "${NATIVE_SERVICE}"
  sudo systemctl restart "${WEB_SERVICE}"
  sudo systemctl restart "${NATIVE_SERVICE}"

  echo
  echo "Done. smart-thermostat-native.service is now the HTML WebView display."
  echo "You should NOT see native/thermostat_native.py below. You SHOULD see native/html_panel.py."
  echo
  ps -ef | grep -E "thermostat_native.py|html_panel.py|hybrid-xinit|xinit" | grep -v grep || true
  echo
  systemctl --no-pager --full status "${WEB_SERVICE}" "${NATIVE_SERVICE}" | sed -n '1,100p' || true
}

main "$@"
