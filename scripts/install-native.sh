#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this with sudo: sudo ./scripts/install-native.sh" >&2
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Prefer the real owner of the project folder. The panel Fetch Update path runs
# this installer from a root-owned transient systemd unit, where SUDO_USER and
# logname can be empty or wrong. If we guess the wrong user here, the native
# service gets written with the wrong HOME/XAUTHORITY and the screen can land at
# a black tty with a blinking cursor.
APP_USER="${SUDO_USER:-}"
if [[ -z "$APP_USER" || "$APP_USER" == "root" ]]; then
  APP_USER="$(stat -c '%U' "$APP_DIR" 2>/dev/null || true)"
fi
if [[ -z "$APP_USER" || "$APP_USER" == "UNKNOWN" || "$APP_USER" == "root" ]]; then
  APP_USER="$(logname 2>/dev/null || true)"
fi
if [[ -z "$APP_USER" || "$APP_USER" == "root" ]]; then
  if id david >/dev/null 2>&1; then
    APP_USER="david"
  elif id pi >/dev/null 2>&1; then
    APP_USER="pi"
  else
    APP_USER="root"
  fi
fi
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
if [[ -z "$APP_HOME" || ! -d "$APP_HOME" ]]; then
  APP_HOME="$(eval echo "~${APP_USER}")"
fi

if [[ ! -f "$APP_DIR/server.py" || ! -f "$APP_DIR/native_app/main.py" ]]; then
  echo "This does not look like the SmartThermostatNative folder: $APP_DIR" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 \
  python3-pyqt5 \
  avahi-daemon \
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
MOUNT_BIN="$(command -v mount || echo /usr/bin/mount)"
UMOUNT_BIN="$(command -v umount || echo /usr/bin/umount)"

# USB config backups must never mount inside the Git checkout. Older builds used
# data/usb-mounts, which can make git clean fail with "Device or resource busy".
# Clean that legacy folder up and use a runtime folder outside the repo instead.
LEGACY_USB_ROOT="$APP_DIR/data/usb-mounts"
if [[ -d "$LEGACY_USB_ROOT" ]]; then
  python3 - <<PY_CLEANUP
from pathlib import Path
import shutil, subprocess
root = Path(${LEGACY_USB_ROOT@Q})
mounts = []
try:
    for line in Path('/proc/mounts').read_text(errors='ignore').splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        mount = Path(parts[1].replace('\\040', ' '))
        try:
            mount.resolve().relative_to(root.resolve())
            mounts.append(mount)
        except Exception:
            continue
except Exception as exc:
    print(f'Could not inspect legacy USB mounts: {exc}')
for mount in sorted(mounts, key=lambda p: len(str(p)), reverse=True):
    print(f'Unmounting legacy USB mount: {mount}')
    subprocess.run(['umount', str(mount)], check=False)
shutil.rmtree(root, ignore_errors=True)
PY_CLEANUP
fi
RUNTIME_USB_ROOT="/tmp/smart-thermostat-usb"
mkdir -p "$RUNTIME_USB_ROOT"
chown "$APP_USER:$APP_USER" "$RUNTIME_USB_ROOT" 2>/dev/null || true

cat >"$SUDOERS_FILE" <<EOF
$APP_USER ALL=(root) NOPASSWD: $SYSTEMD_RUN_BIN, $SYSTEMCTL_BIN, $MOUNT_BIN, $UMOUNT_BIN
EOF
chmod 0440 "$SUDOERS_FILE"

# Advertise the native thermostat backend to Home Assistant over mDNS/DNS-SD.
# The HA custom integration listens for _iha-thermostat._tcp.local.; this Avahi
# service makes the native-only panel discoverable without needing the old web UI.
mkdir -p /etc/avahi/services
python3 - <<PY_AVAHI
from pathlib import Path
import html
import json
import os
import socket

app_dir = Path(${APP_DIR@Q})
service_path = Path('/etc/avahi/services/iha-thermostat.service')

def safe_slug(value: str) -> str:
    return ''.join(ch.lower() if ch.isalnum() else '-' for ch in value).strip('-')

def read_name() -> str:
    for candidate in (app_dir / 'data' / 'thermostat-state.json', app_dir / 'data' / 'panel-config.json'):
        try:
            data = json.loads(candidate.read_text(encoding='utf-8'))
        except Exception:
            continue
        for path in (('thermostat', 'name'), ('config', 'thermostat', 'name')):
            current = data
            for key in path:
                current = current.get(key) if isinstance(current, dict) else None
            if isinstance(current, str) and current.strip():
                return current.strip()[:80]
    return 'IHA Thermostat'

def stable_serial() -> str:
    configured = os.environ.get('SMART_THERMOSTAT_SERIAL', '').strip()
    if configured:
        return configured
    for machine_path in (Path('/etc/machine-id'), Path('/var/lib/dbus/machine-id')):
        try:
            machine_id = machine_path.read_text(encoding='utf-8').strip()
        except OSError:
            machine_id = ''
        if machine_id:
            return f'iha-smart-thermostat-{machine_id[:12]}'
    hostname = safe_slug(socket.gethostname() or 'local')
    return f'iha-smart-thermostat-{hostname or "local"}'

name = read_name()
serial = stable_serial()
xml = f"""<?xml version="1.0" standalone='no'?><!--*-nxml-*-->
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">{html.escape(name)} on %h</name>
  <service>
    <type>_iha-thermostat._tcp</type>
    <port>8080</port>
    <txt-record>path=/api/discovery</txt-record>
    <txt-record>api_path=/api</txt-record>
    <txt-record>serial={html.escape(serial)}</txt-record>
    <txt-record>name={html.escape(name)}</txt-record>
  </service>
</service-group>
"""
service_path.write_text(xml, encoding='utf-8')
print(f'Wrote Home Assistant discovery service: {service_path} ({serial})')
PY_AVAHI
systemctl enable --now avahi-daemon.service 2>/dev/null || true
systemctl restart avahi-daemon.service 2>/dev/null || true

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
