#!/usr/bin/env bash
set -euo pipefail

# Force the wall screen to show the same HTML UI that is available at
# http://<pi>:8080, without running full Chromium kiosk.
#
# This script is intentionally more aggressive than display-mode.sh because it
# is meant to fix units that are still booting into the old blocky Tk/canvas
# native display after an update.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_USER="$(id -un)"
INSTALL_UID="$(id -u)"
WEB_SERVICE="smart-thermostat-web.service"
HYBRID_SERVICE="smart-thermostat-hybrid.service"
NATIVE_SERVICE="smart-thermostat-native.service"
KIOSK_SERVICE="smart-thermostat-kiosk.service"

if [[ $EUID -eq 0 ]]; then
  echo "Run this as the normal pi/user account, not root. It will use sudo where needed." >&2
  exit 1
fi

install_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    return 0
  fi

  local packages=(
    python3
    python3-gi
    xinit
    openbox
    x11-xserver-utils
    unclutter
    dbus-x11
    gir1.2-gtk-3.0
  )

  local webkit_pkg=""
  if apt-cache show gir1.2-webkit2-4.1 >/dev/null 2>&1; then
    webkit_pkg="gir1.2-webkit2-4.1"
  elif apt-cache show gir1.2-webkit2-4.0 >/dev/null 2>&1; then
    webkit_pkg="gir1.2-webkit2-4.0"
  fi

  if [[ -z "${webkit_pkg}" ]]; then
    echo "ERROR: Could not find gir1.2-webkit2-4.1 or gir1.2-webkit2-4.0 in apt." >&2
    echo "The HTML hybrid display needs WebKitGTK. The web UI can still run in a browser, but the wall panel cannot host HTML without a browser engine." >&2
    exit 1
  fi
  packages+=("${webkit_pkg}")

  sudo apt-get update
  sudo apt-get install -y "${packages[@]}"
}

install_service_template() {
  local service_name="$1"
  local source_path="${PROJECT_DIR}/systemd/${service_name}"
  local tmp_path="/tmp/${service_name}"
  local dest_path="/etc/systemd/system/${service_name}"

  if [[ ! -f "${source_path}" ]]; then
    echo "Missing service template: ${source_path}" >&2
    exit 1
  fi

  sed \
    -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    -e "s|__USER__|${INSTALL_USER}|g" \
    -e "s|__UID__|${INSTALL_UID}|g" \
    "${source_path}" > "${tmp_path}"
  sudo cp "${tmp_path}" "${dest_path}"
  rm -f "${tmp_path}"
}

write_env() {
  sudo install -d -m 700 /etc/smart-thermostat

  sudo tee /etc/smart-thermostat/hybrid.env >/dev/null <<'EOF_HYBRID'
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
EOF_HYBRID
  sudo chmod 600 /etc/smart-thermostat/hybrid.env

  # Keep native.env present for old service references, but make the legacy
  # native launcher forward to the same HTML host.
  sudo tee /etc/smart-thermostat/native.env >/dev/null <<'EOF_NATIVE'
SMART_THERMOSTAT_URL=http://127.0.0.1:8080
SMART_THERMOSTAT_API=http://127.0.0.1:8080
SMART_NATIVE_HEALTH_TIMEOUT_SECONDS=75
SMART_NATIVE_ROTATION=left
SMART_NATIVE_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
SMART_HYBRID_FULLSCREEN=1
SMART_HYBRID_WIDTH=1280
SMART_HYBRID_HEIGHT=800
EOF_NATIVE
  sudo chmod 600 /etc/smart-thermostat/native.env
}

main() {
  echo "Installing lightweight HTML wall-display runtime packages..."
  install_packages

  echo "Making launchers executable..."
  chmod +x \
    "${PROJECT_DIR}/native/html_panel.py" \
    "${PROJECT_DIR}/scripts/hybrid-launch.sh" \
    "${PROJECT_DIR}/scripts/hybrid-xinit.sh" \
    "${PROJECT_DIR}/scripts/native-launch.sh" \
    "${PROJECT_DIR}/scripts/native-xinit.sh" \
    "${PROJECT_DIR}/scripts/display-mode.sh" \
    "${PROJECT_DIR}/scripts/appliance-mode.sh" \
    "${PROJECT_DIR}/scripts/force-html-display.sh" 2>/dev/null || true

  echo "Installing systemd units..."
  install_service_template "${WEB_SERVICE}"
  install_service_template "${HYBRID_SERVICE}"
  install_service_template "${NATIVE_SERVICE}"
  write_env

  echo "Stopping old display paths..."
  sudo systemctl stop "${NATIVE_SERVICE}" "${KIOSK_SERVICE}" "${HYBRID_SERVICE}" >/dev/null 2>&1 || true
  sudo pkill -f "native/thermostat_native.py" >/dev/null 2>&1 || true
  sudo pkill -f "scripts/kiosk-launch.sh" >/dev/null 2>&1 || true
  sudo pkill -f "chromium.*127.0.0.1:8080" >/dev/null 2>&1 || true

  sudo systemctl disable "${NATIVE_SERVICE}" "${KIOSK_SERVICE}" >/dev/null 2>&1 || true
  sudo systemctl disable display-manager.service lightdm.service gdm.service >/dev/null 2>&1 || true
  sudo systemctl set-default multi-user.target >/dev/null 2>&1 || true
  sudo rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 2>/dev/null || true

  echo "Starting HTML hybrid wall display..."
  sudo systemctl daemon-reload
  sudo systemctl enable --now "${WEB_SERVICE}"
  sudo systemctl enable --now "${HYBRID_SERVICE}"
  sudo systemctl restart "${WEB_SERVICE}" "${HYBRID_SERVICE}"

  echo
  echo "Done. The wall screen should now show the same HTML UI as http://127.0.0.1:8080 in the lightweight native WebKit host."
  echo "Status:"
  systemctl --no-pager --full status "${WEB_SERVICE}" "${HYBRID_SERVICE}" | sed -n '1,80p' || true
  echo
  echo "If the screen does not change within 10 seconds, run:"
  echo "  journalctl -u ${HYBRID_SERVICE} -n 120 --no-pager"
}

main "$@"
