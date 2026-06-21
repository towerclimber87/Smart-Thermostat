#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

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
  APP_HOME="$HOME"
fi
BACKUP_ROOT="$APP_HOME/thermostat-pi-data-backups"

run_as_app_user() {
  if [[ "$(id -u)" -eq 0 && "$APP_USER" != "root" ]]; then
    if command -v runuser >/dev/null 2>&1; then
      runuser -u "$APP_USER" -- "$@"
    else
      su -s /bin/bash "$APP_USER" -c "$(printf '%q ' "$@")"
    fi
  else
    "$@"
  fi
}

cleanup_legacy_usb_mounts() {
  local root="$APP_DIR/data/usb-mounts"
  [[ -d "$root" ]] || return 0
  python3 - <<PY_CLEANUP
from pathlib import Path
import shutil, subprocess
root = Path(${root@Q})
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
}

ts="$(date +%F-%H%M%S)"
backup_dir="$BACKUP_ROOT/$ts"
mkdir -p "$backup_dir"
cleanup_legacy_usb_mounts
for file in panel-config.json thermostat-state.json hvac-history.json; do
  if [[ -f "data/$file" ]]; then
    cp -av "data/$file" "$backup_dir/$file"
  fi
done

run_as_app_user git fetch origin Development
run_as_app_user git reset --hard origin/Development
cleanup_legacy_usb_mounts
run_as_app_user git clean -fd

mkdir -p data
if compgen -G "$backup_dir/*" >/dev/null; then
  cp -av "$backup_dir/." data/.
fi
chmod +x scripts/*.sh
sudo ./scripts/install-native.sh
sudo systemctl daemon-reload
sudo systemctl restart smart-thermostat-backend.service smart-thermostat-native.service
sleep 8
systemctl status smart-thermostat-backend.service smart-thermostat-native.service --no-pager -l
