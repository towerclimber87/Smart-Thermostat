#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_SERVICE_NAME="smart-thermostat-web.service"
KIOSK_SERVICE_NAME="smart-thermostat-kiosk.service"
UPDATE_AGENT_SERVICE_NAME="smart-thermostat-update-agent.service"
NETWORK_WATCHDOG_SERVICE_NAME="smart-thermostat-network-watchdog.service"

if [[ $EUID -eq 0 ]]; then
  echo "Run this script as the pi/user account, not root. It will use sudo when needed."
  exit 1
fi

install_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    return 0
  fi

  sudo apt-get update

  local packages=(
    avahi-daemon
    git
    python3
    python3-zeroconf
    x11-xserver-utils
    unclutter
  )

  if apt-cache show chromium-browser >/dev/null 2>&1; then
    packages+=(chromium-browser)
  elif apt-cache show chromium >/dev/null 2>&1; then
    packages+=(chromium)
  else
    echo "WARNING: Could not find chromium-browser or chromium in apt. Install Chromium before enabling kiosk mode." >&2
  fi

  sudo apt-get install -y "${packages[@]}"
}

install_service() {
  local service_name="$1"
  local source_path="${PROJECT_DIR}/systemd/${service_name}"
  local service_path="/etc/systemd/system/${service_name}"

  if [[ ! -f "${source_path}" ]]; then
    echo "Skipping optional service ${service_name}; ${source_path} is not included in this build."
    return 0
  fi

  local user_id
  user_id="$(id -u)"
  sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" -e "s|__USER__|${USER}|g" -e "s|__UID__|${user_id}|g" "${source_path}" > "/tmp/${service_name}"
  sudo cp "/tmp/${service_name}" "${service_path}"
}

install_sudoers() {
  local systemctl_bin
  systemctl_bin="$(command -v systemctl || echo /usr/bin/systemctl)"
  sudo tee /etc/sudoers.d/smart-thermostat-panel >/dev/null <<EOF_SUDOERS
${USER} ALL=(root) NOPASSWD: ${systemctl_bin} reboot, ${systemctl_bin} restart ${WEB_SERVICE_NAME}
EOF_SUDOERS
  sudo chmod 440 /etc/sudoers.d/smart-thermostat-panel
}

install_packages
chmod +x "${PROJECT_DIR}/scripts/install-pi.sh" "${PROJECT_DIR}/scripts/kiosk-launch.sh" "${PROJECT_DIR}/scripts/network_watchdog.py" 2>/dev/null || true

install_service "${WEB_SERVICE_NAME}"
install_service "${KIOSK_SERVICE_NAME}"
install_service "${UPDATE_AGENT_SERVICE_NAME}"
install_service "${NETWORK_WATCHDOG_SERVICE_NAME}"

sudo install -d -m 700 /etc/smart-thermostat
if [[ ! -f /etc/smart-thermostat/network-watchdog.env ]]; then
  sudo tee /etc/smart-thermostat/network-watchdog.env >/dev/null <<'EOF_NETWORK'
NETWORK_WATCHDOG_INTERVAL_SECONDS=60
NETWORK_WATCHDOG_TARGET=1.1.1.1
NETWORK_WATCHDOG_ETHERNET_METRIC=50
NETWORK_WATCHDOG_WIFI_METRIC=600
EOF_NETWORK
  sudo chmod 600 /etc/smart-thermostat/network-watchdog.env
fi

if [[ ! -f /etc/smart-thermostat/kiosk.env ]]; then
  sudo tee /etc/smart-thermostat/kiosk.env >/dev/null <<'EOF_KIOSK'
SMART_THERMOSTAT_URL=http://127.0.0.1:8080
SMART_KIOSK_PROFILE_DIR=/tmp/smart-thermostat-chromium-profile
SMART_KIOSK_CACHE_DIR=/tmp/smart-thermostat-chromium-cache
SMART_KIOSK_HEALTH_TIMEOUT_SECONDS=45
# auto, wayland, or x11. Auto lets the launcher use Wayland when the desktop exposes it.
SMART_KIOSK_OZONE_PLATFORM=auto
# Optional extra Chromium flags. Example:
# SMART_KIOSK_EXTRA_FLAGS=--force-device-scale-factor=1
SMART_KIOSK_EXTRA_FLAGS=
EOF_KIOSK
  sudo chmod 600 /etc/smart-thermostat/kiosk.env
fi

install_sudoers

sudo systemctl daemon-reload
sudo systemctl enable --now "${WEB_SERVICE_NAME}"
sudo systemctl enable --now "${NETWORK_WATCHDOG_SERVICE_NAME}"
sudo systemctl enable "${KIOSK_SERVICE_NAME}"
if [[ -f "/etc/systemd/system/${UPDATE_AGENT_SERVICE_NAME}" ]]; then
  sudo systemctl enable --now "${UPDATE_AGENT_SERVICE_NAME}" || true
fi

if systemctl get-default | grep -q '^multi-user.target$'; then
  echo "NOTE: this Pi is set to boot to console. Kiosk mode needs the graphical desktop target."
  echo "Run: sudo systemctl set-default graphical.target"
fi

echo "IHA web service, Chromium kiosk service, and network watchdog installed."
echo "Web UI: http://localhost:8080"
echo "Kiosk service: sudo systemctl start ${KIOSK_SERVICE_NAME}"
echo "Home Assistant discovery uses mDNS service _iha-thermostat._tcp.local."
