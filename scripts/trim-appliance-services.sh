#!/usr/bin/env bash
set -euo pipefail

echo "Disabling services that are not needed by the thermostat appliance..."

sudo systemctl disable --now bluetooth.service hciuart.service >/dev/null 2>&1 || true

# NFS/RPC services are not used by the thermostat panel.  Only disable them when
# there are no active NFS mounts so we do not break a system that is deliberately
# booting or logging to NFS.
if ! awk '$3 ~ /^nfs/ { found=1 } END { exit found ? 0 : 1 }' /proc/mounts; then
  sudo systemctl disable --now nfs-blkmap.service rpcbind.service rpcbind.socket >/dev/null 2>&1 || true
else
  echo "NFS mount detected; leaving nfs-blkmap/rpcbind alone."
fi

sudo systemctl daemon-reload

echo "Remaining running services:"
systemctl list-units --type=service --state=running --no-pager
