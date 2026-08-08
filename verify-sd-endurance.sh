#!/usr/bin/env bash
set -u

EXPECTED_VERSION="15.98-08-08-26"
APP_USER="${SUDO_USER:-${USER:-david}}"
[[ -n "$APP_USER" && "$APP_USER" != "root" ]] || APP_USER="david"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6 2>/dev/null || true)"
[[ -n "$APP_HOME" ]] || APP_HOME="/home/$APP_USER"
APP_DIR="${APP_DIR:-$APP_HOME/Smart-Thermostat-Development}"
FAIL=0

ok()   { printf 'PASS  %s\n' "$*"; }
bad()  { printf 'FAIL  %s\n' "$*"; FAIL=1; }
info() { printf 'INFO  %s\n' "$*"; }

echo "Smart Thermostat SD Endurance Verification"
echo

VERSION="$(tr -d '\r\n' < "$APP_DIR/VERSION" 2>/dev/null || true)"
[[ "$VERSION" == "$EXPECTED_VERSION" ]] && ok "application version $VERSION" || bad "application version is '${VERSION:-missing}', expected $EXPECTED_VERSION"

ROOT_OPTS="$(findmnt -no OPTIONS / 2>/dev/null || true)"
[[ ",$ROOT_OPTS," == *,noatime,* ]] && ok "root filesystem mounted noatime" || bad "root filesystem is not mounted noatime"

SWAPS="$(awk 'NR>1 {print $1}' /proc/swaps 2>/dev/null)"
if [[ "$SWAPS" == "/dev/zram0" ]]; then
  ok "only /dev/zram0 is active swap"
else
  bad "active swap devices are: ${SWAPS//$'\n'/, }"
fi

BACKING="$(cat /sys/block/zram0/backing_dev 2>/dev/null || true)"
[[ "$BACKING" == "none" ]] && ok "zram has no disk backing device" || bad "zram backing device is '${BACKING:-unknown}'"

if systemctl list-timers --all --no-pager 2>/dev/null | grep -q 'rpi-zram-writeback.timer'; then
  bad "rpi-zram-writeback.timer is still scheduled"
else
  ok "no zram disk-writeback timer"
fi

[[ ! -e /var/swap ]] && ok "/var/swap is absent" || bad "/var/swap still exists"

if [[ -f /etc/systemd/journald.conf.d/90-smart-thermostat-sd-endurance.conf ]] && \
   grep -Eq '^[[:space:]]*Storage=volatile[[:space:]]*$' /etc/systemd/journald.conf.d/90-smart-thermostat-sd-endurance.conf; then
  ok "journald configured for volatile storage"
else
  bad "volatile journald drop-in missing or incorrect"
fi

if systemctl is-active --quiet rsyslog.service 2>/dev/null; then
  bad "rsyslog is active"
else
  ok "rsyslog inactive/not installed"
fi

if systemctl is-active --quiet smart-thermostat-backend.service 2>/dev/null; then
  ok "thermostat backend service active"
else
  bad "thermostat backend service not active"
fi

if systemctl is-active --quiet smart-thermostat-native.service 2>/dev/null; then
  ok "thermostat native UI service active"
else
  bad "thermostat native UI service not active"
fi

MEM_AVAIL="$(awk '/MemAvailable:/ {printf "%.0f MiB", $2/1024}' /proc/meminfo 2>/dev/null || true)"
[[ -n "$MEM_AVAIL" ]] && info "currently available RAM: $MEM_AVAIL"

if [[ $FAIL -eq 0 ]]; then
  echo
  echo "ALL SD-ENDURANCE CHECKS PASSED"
  exit 0
fi

echo
echo "ONE OR MORE CHECKS FAILED -- do not make additional changes until reviewed."
exit 1
