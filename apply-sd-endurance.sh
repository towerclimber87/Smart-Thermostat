#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_VERSION="15.98-08-08-26"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="$SCRIPT_DIR/payload/Smart-Thermostat-Development"
NO_REBOOT=0

if [[ "${1:-}" == "--no-reboot" ]]; then
  NO_REBOOT=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: sudo ./apply-sd-endurance.sh [--no-reboot]" >&2
  exit 2
fi

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo: sudo ./apply-sd-endurance.sh" >&2
  exit 1
fi

APP_USER="${SUDO_USER:-david}"
if [[ -z "$APP_USER" || "$APP_USER" == "root" ]]; then
  APP_USER="david"
fi
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6 || true)"
[[ -n "$APP_HOME" ]] || APP_HOME="/home/$APP_USER"
APP_DIR="${APP_DIR:-$APP_HOME/Smart-Thermostat-Development}"

fail() {
  echo
  echo "ERROR: $*" >&2
  echo "No reboot was requested because deployment did not complete cleanly." >&2
  exit 1
}

[[ -d "$APP_DIR" ]] || fail "Thermostat application directory not found: $APP_DIR"
[[ -f "$PAYLOAD_DIR/VERSION" ]] || fail "Payload is incomplete. Missing VERSION."
PAYLOAD_VERSION="$(tr -d '\r\n' < "$PAYLOAD_DIR/VERSION")"
[[ "$PAYLOAD_VERSION" == "$EXPECTED_VERSION" ]] || fail "Unexpected payload version: $PAYLOAD_VERSION"
command -v rpi-systemd-config >/dev/null 2>&1 || fail "rpi-systemd-config is not installed."
command -v systemctl >/dev/null 2>&1 || fail "systemd is not available."

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/var/backups/smart-thermostat-sd-endurance/$STAMP"
mkdir -p "$BACKUP_DIR/app"
chmod 0700 "$BACKUP_DIR"

echo "Smart Thermostat SD Endurance Deployment"
echo "Application: $APP_DIR"
echo "Target version: $EXPECTED_VERSION"
echo "Backup: $BACKUP_DIR"
echo

FILES=(
  "VERSION"
  "server.py"
  "native_app/main.py"
  "scripts/install-native.sh"
  "scripts/run-native.sh"
  "systemd/smart-thermostat-backend.service.template"
)

echo "[1/5] Backing up the six application files being replaced..."
for rel in "${FILES[@]}"; do
  if [[ -e "$APP_DIR/$rel" ]]; then
    mkdir -p "$BACKUP_DIR/app/$(dirname "$rel")"
    cp -a "$APP_DIR/$rel" "$BACKUP_DIR/app/$rel"
  fi
done
if [[ -f /etc/rpi/swap.conf.d/90-thermostat-zram-only.conf ]]; then
  mkdir -p "$BACKUP_DIR/system"
  cp -a /etc/rpi/swap.conf.d/90-thermostat-zram-only.conf "$BACKUP_DIR/system/"
fi
if [[ -f /etc/systemd/journald.conf.d/90-smart-thermostat-sd-endurance.conf ]]; then
  mkdir -p "$BACKUP_DIR/system"
  cp -a /etc/systemd/journald.conf.d/90-smart-thermostat-sd-endurance.conf "$BACKUP_DIR/system/"
fi

copy_if_changed() {
  local rel="$1"
  local mode="$2"
  local src="$PAYLOAD_DIR/$rel"
  local dst="$APP_DIR/$rel"
  local tmp
  [[ -f "$src" ]] || fail "Payload file missing: $rel"
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    echo "  unchanged: $rel"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  tmp="$(mktemp "$(dirname "$dst")/.sd-endurance.XXXXXX")"
  install -m "$mode" "$src" "$tmp"
  chown "$APP_USER:$APP_USER" "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$dst"
  echo "  updated:   $rel"
}

restore_app_backup() {
  echo "Installer failed; restoring the application files from $BACKUP_DIR/app ..." >&2
  for rel in "${FILES[@]}"; do
    if [[ -e "$BACKUP_DIR/app/$rel" ]]; then
      mkdir -p "$APP_DIR/$(dirname "$rel")"
      cp -a "$BACKUP_DIR/app/$rel" "$APP_DIR/$rel"
    fi
  done
  if [[ -x "$APP_DIR/scripts/install-native.sh" ]]; then
    "$APP_DIR/scripts/install-native.sh" >/dev/null 2>&1 || true
  fi
}

echo "[2/5] Installing the validated application-side SD endurance changes..."
copy_if_changed "VERSION" 0644
copy_if_changed "server.py" 0644
copy_if_changed "native_app/main.py" 0644
copy_if_changed "scripts/install-native.sh" 0755
copy_if_changed "scripts/run-native.sh" 0755
copy_if_changed "systemd/smart-thermostat-backend.service.template" 0644

if ! "$APP_DIR/scripts/install-native.sh"; then
  restore_app_backup
  fail "Native installer failed; application files were rolled back."
fi

INSTALLED_VERSION="$(tr -d '\r\n' < "$APP_DIR/VERSION" 2>/dev/null || true)"
[[ "$INSTALLED_VERSION" == "$EXPECTED_VERSION" ]] || fail "Application version verification failed: $INSTALLED_VERSION"

echo "[3/5] Forcing pure zram swap (no SD-card backing file)..."
install -d -m 0755 /etc/rpi/swap.conf.d
TMP_SWAP="$(mktemp)"
printf '[Main]\nMechanism=zram\n' > "$TMP_SWAP"
if [[ ! -f /etc/rpi/swap.conf.d/90-thermostat-zram-only.conf ]] || ! cmp -s "$TMP_SWAP" /etc/rpi/swap.conf.d/90-thermostat-zram-only.conf; then
  install -m 0644 "$TMP_SWAP" /etc/rpi/swap.conf.d/90-thermostat-zram-only.conf
fi
rm -f "$TMP_SWAP"

RESOLVED_SWAP="$(rpi-systemd-config rpi/swap.conf RESULT Main::Mechanism 2>/dev/null || true)"
[[ "$RESOLVED_SWAP" == *"'zram'"* ]] || fail "rpi-swap did not resolve Mechanism=zram: $RESOLVED_SWAP"
echo "  rpi-swap resolved: $RESOLVED_SWAP"

echo "[4/5] Verifying the already-validated low-write OS settings..."
ROOT_OPTS="$(findmnt -no OPTIONS / 2>/dev/null || true)"
if [[ ",$ROOT_OPTS," == *,noatime,* ]]; then
  echo "  root filesystem: noatime OK"
else
  echo "  WARNING: root filesystem is not mounted noatime; this script intentionally did not edit /etc/fstab."
fi

if [[ -f /etc/systemd/journald.conf.d/90-smart-thermostat-sd-endurance.conf ]] && \
   grep -Eq '^[[:space:]]*Storage=volatile[[:space:]]*$' /etc/systemd/journald.conf.d/90-smart-thermostat-sd-endurance.conf; then
  echo "  journal: volatile/RAM-backed OK"
else
  fail "Expected volatile journald configuration was not installed."
fi

if systemctl is-active --quiet rsyslog.service 2>/dev/null; then
  echo "  WARNING: rsyslog is active. It was not disabled automatically."
else
  echo "  rsyslog: inactive/not installed OK"
fi

echo "[5/5] Pre-reboot checks complete."
echo
cat <<SUMMARY
Deployment is ready for reboot.
After reboot, the expected state is:
  - /dev/zram0 is the only active swap
  - /sys/block/zram0/backing_dev = none
  - no rpi-zram-writeback timer
  - /var/swap removed by rpi-swap
  - root remains noatime
  - journald remains volatile
  - thermostat app version $EXPECTED_VERSION
SUMMARY

echo
if [[ $NO_REBOOT -eq 1 ]]; then
  echo "--no-reboot selected. Reboot manually when convenient: sudo reboot"
  exit 0
fi

echo "Rebooting now to apply pure-zram mode..."
sync
systemctl reboot
