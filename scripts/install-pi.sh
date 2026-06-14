#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="smart-thermostat-web.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

if [[ $EUID -eq 0 ]]; then
  echo "Run this script as the pi/user account, not root. It will use sudo when needed."
  exit 1
fi

sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" -e "s|__USER__|${USER}|g" "${PROJECT_DIR}/systemd/${SERVICE_NAME}" > "/tmp/${SERVICE_NAME}"

sudo cp "/tmp/${SERVICE_NAME}" "${SERVICE_PATH}"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

echo "Smart Thermostat web service installed."
echo "Open http://localhost:8080 on the Raspberry Pi."
