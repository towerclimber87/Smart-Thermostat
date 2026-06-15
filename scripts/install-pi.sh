#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_SERVICE_NAME="smart-thermostat-web.service"
UPDATE_AGENT_SERVICE_NAME="smart-thermostat-update-agent.service"
NETWORK_WATCHDOG_SERVICE_NAME="smart-thermostat-network-watchdog.service"

if [[ $EUID -eq 0 ]]; then
  echo "Run this script as the pi/user account, not root. It will use sudo when needed."
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y avahi-daemon git python3 python3-zeroconf
fi

install_service() {
  local service_name="$1"
  local service_path="/etc/systemd/system/${service_name}"
  sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" -e "s|__USER__|${USER}|g" "${PROJECT_DIR}/systemd/${service_name}" > "/tmp/${service_name}"
  sudo cp "/tmp/${service_name}" "${service_path}"
}

install_service "${WEB_SERVICE_NAME}"
install_service "${UPDATE_AGENT_SERVICE_NAME}"
install_service "${NETWORK_WATCHDOG_SERVICE_NAME}"

sudo install -d -m 700 /etc/smart-thermostat
if [[ ! -f /etc/smart-thermostat/network-watchdog.env ]]; then
  sudo tee /etc/smart-thermostat/network-watchdog.env >/dev/null <<'EOF'
NETWORK_WATCHDOG_INTERVAL_SECONDS=60
NETWORK_WATCHDOG_TARGET=1.1.1.1
NETWORK_WATCHDOG_ETHERNET_METRIC=50
NETWORK_WATCHDOG_WIFI_METRIC=600
EOF
  sudo chmod 600 /etc/smart-thermostat/network-watchdog.env
fi
sudo systemctl daemon-reload
sudo systemctl enable --now "${WEB_SERVICE_NAME}"
sudo systemctl enable --now "${UPDATE_AGENT_SERVICE_NAME}"
sudo systemctl enable --now "${NETWORK_WATCHDOG_SERVICE_NAME}"

echo "IHA web service, remote update agent, and network watchdog installed."
echo "Open http://localhost:8080 on the Raspberry Pi."
echo "Set /etc/smart-thermostat/update-agent.env before the updater agent can check in."
echo "Home Assistant discovery uses mDNS service _iha-thermostat._tcp.local."
