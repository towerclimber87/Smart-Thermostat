#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this with sudo: sudo ./scripts/install-native.sh" >&2
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${SUDO_USER:-$(logname 2>/dev/null || echo david)}"
APP_HOME="$(eval echo "~${APP_USER}")"

if [[ ! -f "$APP_DIR/server.py" || ! -f "$APP_DIR/native_app/main.py" ]]; then
  echo "This does not look like the SmartThermostatNative folder: $APP_DIR" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 \
  python3-pyqt5 \
  xserver-xorg \
  xinit \
  x11-xserver-utils \
  xserver-xorg-legacy \
  unclutter \
  dbus-x11

# Let systemd launch the appliance X server as the pi user.
mkdir -p /etc/X11
cat >/etc/X11/Xwrapper.config <<EOF
allowed_users=anybody
needs_root_rights=yes
EOF

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod +x "$APP_DIR/scripts/native-xinit.sh" "$APP_DIR/scripts/run-native.sh"

# Stop and disable browser/web kiosk services if they exist. The new backend still listens on 8080 for local API calls.
for svc in smart-thermostat-kiosk.service smart-thermostat-web.service smart-thermostat-native.service; do
  systemctl stop "$svc" 2>/dev/null || true
  systemctl disable "$svc" 2>/dev/null || true
done

# The native UI owns tty1. If getty keeps tty1, xinit can hang and leave the terminal visible.
systemctl disable --now getty@tty1.service 2>/dev/null || true

# Make sure the touch UI user can access display, input, and GPU devices on Raspberry Pi OS.
usermod -aG tty,video,input,render "$APP_USER" 2>/dev/null || true

# Let the native panel start the self-update transient systemd unit without a
# password prompt. The actual update runs as a separate root-owned systemd unit,
# outside the backend service cgroup, so restarting the backend will not kill it.
SUDOERS_FILE="/etc/sudoers.d/smart-thermostat-panel"
SYSTEMD_RUN_BIN="$(command -v systemd-run || echo /usr/bin/systemd-run)"
SYSTEMCTL_BIN="$(command -v systemctl || echo /usr/bin/systemctl)"
cat >"$SUDOERS_FILE" <<EOF
$APP_USER ALL=(root) NOPASSWD: $SYSTEMD_RUN_BIN, $SYSTEMCTL_BIN
EOF
chmod 0440 "$SUDOERS_FILE"


sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@APP_USER@|$APP_USER|g" -e "s|@APP_HOME@|$APP_HOME|g" \
  "$APP_DIR/systemd/smart-thermostat-backend.service.template" > /etc/systemd/system/smart-thermostat-backend.service
sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@APP_USER@|$APP_USER|g" -e "s|@APP_HOME@|$APP_HOME|g" \
  "$APP_DIR/systemd/smart-thermostat-native.service.template" > /etc/systemd/system/smart-thermostat-native.service

systemctl daemon-reload
systemctl enable smart-thermostat-backend.service
systemctl enable smart-thermostat-native.service
systemctl restart smart-thermostat-backend.service
systemctl restart smart-thermostat-native.service

echo "Installed SmartThermostatNative from $APP_DIR"
echo "Backend: systemctl status smart-thermostat-backend.service"
echo "Native UI: systemctl status smart-thermostat-native.service"
